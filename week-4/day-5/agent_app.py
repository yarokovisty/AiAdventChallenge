"""
Week 4 · Day 5 — MCP Orchestration: мульти-серверный пайплайн.

Задание: зарегистрировать несколько MCP-серверов; агент должен сам выбрать
инструменты, маршрутизировать вызовы и выполнить длинный сценарий с
инструментами разных серверов.

Приложение сводит четыре роли:

  1. РЕГИСТРАЦИЯ — поднимает ЧЕТЫРЕ MCP-сервера одновременно (каждый своим
     подпроцессом stdio) и собирает их инструменты в один реестр:

       weather   ← mcp_weather_server.py         (day-2, Open-Meteo:
                   geocode_city, get_weather_forecast)
       journal   ← mcp_weather_journal_server.py (day-3, SQLite + планировщик:
                   watch_city, collect_now, get_weather_stats, …)
       pipeline  ← mcp_pipeline_server.py        (day-4, конвейер с артефактами:
                   run_pipeline, inspect_run, …)
       travel    ← mcp_travel_kit_server.py      (day-5, новый, без сети:
                   recommend_clothing, build_checklist, score_travel_weather)

  2. МАРШРУТИЗАЦИЯ — каждому инструменту даётся полное имя «сервер__инструмент»
     (weather__geocode_city, journal__collect_now, …). Модель вызывает
     инструмент по полному имени, приложение смотрит в реестр и отправляет
     вызов в сессию нужного сервера. Префикс заодно снимает конфликты имён:
     два сервера спокойно могут иметь инструмент с одинаковым именем.

  3. АГЕНТ — LLM GLM (Z.ai) с tool-calling (мост переиспользован из
     day-2/3/4). Выбор инструментов — за моделью; данные между серверами
     передаёт тоже она (координаты из weather — в travel, агрегаты из
     pipeline — в travel), серверы друг о друге не знают.

  4. ДЕМО — длинный сценарий «спланируй поездку в Сочи» с участием всех
     четырёх серверов (~10 вызовов) + сценарий, где модель сама выбирает
     инструменты без подсказок, каким сервером пользоваться.

Счётчик маршрутизации в конце прогона показывает, сколько вызовов ушло
на каждый сервер, — это и есть доказательство маршрутизации.

Запуск:
    python agent_app.py                # демо: длинный сценарий + свободный выбор
    python agent_app.py "ваш вопрос"   # свой вопрос агенту
    python agent_app.py --smoke        # проверка 4 серверов без LLM
"""

import asyncio
import json
import os
import sys
from collections import Counter
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# OpenAI-совместимый coding-эндпоинт Z.ai (тот же, что в AgentDemo и day-2/3/4).
DEFAULT_ZAI_ENDPOINT = "https://api.z.ai/api/coding/paas/v4/chat/completions"
DEFAULT_MODEL = "glm-4.6"

# Длинный сценарий — до ~11 вызовов; запас на повторы и уточнения модели.
MAX_TOOL_ROUNDS = 16

TOOL_PREFIX_SEP = "__"  # weather__geocode_city — буквы/цифры/подчёркивания,
                        # чтобы имя прошло валидацию tools у LLM


@dataclass
class ServerSpec:
    """MCP-сервер, который приложение поднимает как подпроцесс stdio."""

    alias: str  # короткое имя для префикса инструментов (латиница/подчёркивания)
    script: str  # имя файла сервера рядом с agent_app.py
    env: dict[str, str] = field(default_factory=dict)  # доп. переменные окружения
    about: str = ""  # чем занимается сервер — для системного промпта


SERVERS: list[ServerSpec] = [
    ServerSpec(
        alias="weather",
        script="mcp_weather_server.py",
        about="геокодинг и прогноз погоды по координатам (Open-Meteo)",
    ),
    ServerSpec(
        alias="journal",
        script="mcp_weather_journal_server.py",
        # В day-3 планировщик тикал каждые 15 с ради демонстрации периодичности.
        # Здесь он не нужен: замер в сценарии делает collect_now, поэтому
        # интервал задран до часа — фоновой работы нет, демо не ждёт тиков.
        env={"JOURNAL_INTERVAL_SEC": "3600"},
        about="наблюдение за городами: замеры копятся в SQLite, агрегаты по окну времени",
    ),
    ServerSpec(
        alias="pipeline",
        script="mcp_pipeline_server.py",
        about="конвейер «получение → обработка → сохранение» с артефактами, sha256 и аудитом inspect_run",
    ),
    ServerSpec(
        alias="travel",
        script="mcp_travel_kit_server.py",
        about="советы по одежде, чек-лист сборов и оценка погоды для поездки (правила, без сети)",
    ),
]

SYSTEM_PROMPT = (
    "Ты — ассистент-оркестратор, к которому подключено НЕСКОЛЬКО MCP-серверов. "
    "Имя каждого инструмента начинается с префикса сервера: weather__* — погода "
    "по названию города и прогноз по координатам; journal__* — наблюдение за "
    "городами и накопление замеров; pipeline__* — конвейер анализа погоды с "
    "сохранением отчёта и аудитом; travel__* — одежда, чек-лист и оценка погоды "
    "для поездки. Маршрутизацию делает приложение: вызывай инструмент по полному "
    "имени с префиксом — вызов уйдёт на нужный сервер. Данные между серверами "
    "передаёшь ты сам: координаты из weather__geocode_city подавай в "
    "weather__get_weather_forecast, а агрегаты прогноза (средняя температура, "
    "дождливые дни, ветер) — в travel__* (их возвращает pipeline__run_pipeline "
    "или pipeline__analyze_data). Серверы друг о друге не знают. Отвечай "
    "по-русски и кратко; перечисляй идентификаторы (artifact_id, report_id, "
    "run_id) — по ним видно передачу данных между этапами."
)

# Вызовы smoke-режима: по одному дешёвому инструменту на каждый сервер —
# проверка «все 4 сессии живы и маршрутизация работает» без участия LLM.
SMOKE_CALLS: list[tuple[str, str, dict[str, Any]]] = [
    ("weather", "geocode_city", {"city": "Москва"}),
    ("journal", "list_watches", {}),
    ("pipeline", "list_reports", {}),
    ("travel", "recommend_clothing", {"temperature_c": 5.0, "condition": "морось", "wind_kph": 22.0}),
]


# --------------------------------------------------------------------------- #
# Транспорт к LLM (Z.ai GLM, OpenAI-совместимый протокол) — как в day-2/3/4.
# --------------------------------------------------------------------------- #


async def chat_completion(
    http: httpx.AsyncClient,
    endpoint: str,
    api_key: str,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
) -> dict[str, Any]:
    """Один вызов chat/completions; возвращает message из choices[0]."""
    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "tools": tools,
        "thinking": {"type": "disabled"},  # оркестратору размышления не нужны
    }
    resp = await http.post(
        endpoint,
        headers={"Authorization": f"Bearer {api_key}"},
        json=body,
    )
    data: dict[str, Any] = resp.json()
    if not resp.is_success:
        err = data.get("error") or {}
        raise RuntimeError(f"Z.ai API HTTP {resp.status_code}: {err.get('message') or data}")

    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError(f"Z.ai API вернул пустой choices: {data}")
    return choices[0].get("message") or {}


# --------------------------------------------------------------------------- #
# Мост MCP ⇄ OpenAI tool-calling (адаптация моста из day-2/3/4 под префиксы).
# --------------------------------------------------------------------------- #


def tool_schema(tool: Any, attr: str) -> dict[str, Any] | None:
    """Достаёт схему инструмента, переживая различия имён полей в SDK."""
    value = getattr(tool, attr, None)
    return value or None


def mcp_tool_to_openai(tool: Any, alias: str) -> dict[str, Any]:
    """MCP-инструмент → формат tools OpenAI, с префиксом сервера в имени."""
    input_schema = tool_schema(tool, "input_schema") or tool_schema(tool, "inputSchema") or {}
    return {
        "type": "function",
        "function": {
            "name": f"{alias}{TOOL_PREFIX_SEP}{tool.name}",
            "description": f"[Сервер «{alias}»] {tool.description or ''}",
            "parameters": input_schema,
        },
    }


def tool_result_text(result: Any) -> str:
    """Результат call_tool → текст для сообщения role=tool."""
    is_error = bool(getattr(result, "is_error", False) or getattr(result, "isError", False))
    structured = tool_schema(result, "structured_content") or tool_schema(result, "structuredContent")
    texts = [c.text for c in (result.content or []) if hasattr(c, "text")]

    if is_error:
        return "ОШИБКА ИНСТРУМЕНТА: " + ("; ".join(texts) or "неизвестная ошибка")
    if structured is not None:
        return json.dumps(structured, ensure_ascii=False)
    return "\n".join(texts) or "(пустой результат)"


def parse_tool_args(raw: Any) -> dict[str, Any]:
    """Аргументы tool_call приходят JSON-строкой — разворачиваем в dict."""
    if isinstance(raw, dict):
        return raw
    return json.loads(raw or "{}")


def preview(text: str, limit: int = 340) -> str:
    return text if len(text) <= limit else text[:limit] + " …"


# --------------------------------------------------------------------------- #
# Регистрация серверов: 4 подпроцесса → 4 сессии → один реестр инструментов.
# --------------------------------------------------------------------------- #


@dataclass
class Route:
    """Куда отправить вызов инструмента по его полному имени."""

    alias: str  # префикс/псевдоним сервера
    session: ClientSession  # MCP-сессия этого сервера
    tool_name: str  # исходное имя инструмента на сервере (без префикса)


async def connect_servers(
    stack: AsyncExitStack,
) -> tuple[dict[str, Route], list[dict[str, Any]], dict[str, str]]:
    """Поднимает все серверы, собирает реестр маршрутизации и tools для LLM.

    Возвращает (реестр «полное имя → Route», tools в формате OpenAI,
    справочник «alias → имя сервера из initialize»).
    """
    registry: dict[str, Route] = {}
    openai_tools: list[dict[str, Any]] = []
    display_names: dict[str, str] = {}

    for spec in SERVERS:
        server_params = StdioServerParameters(
            command=sys.executable,
            args=[str(Path(__file__).with_name(spec.script))],
            env={**os.environ, **spec.env},
        )
        read, write = await stack.enter_async_context(stdio_client(server_params))
        session = await stack.enter_async_context(ClientSession(read, write))
        init = await session.initialize()
        display_names[spec.alias] = f"{init.server_info.name} v{init.server_info.version}"

        tools_result = await session.list_tools()
        names = []
        for tool in tools_result.tools:
            full_name = f"{spec.alias}{TOOL_PREFIX_SEP}{tool.name}"
            if full_name in registry:  # префикс снимает конфликты, но проверим
                raise RuntimeError(f"Дубликат имени инструмента: {full_name}")
            registry[full_name] = Route(alias=spec.alias, session=session, tool_name=tool.name)
            openai_tools.append(mcp_tool_to_openai(tool, spec.alias))
            names.append(f"{spec.alias}{TOOL_PREFIX_SEP}{tool.name}")

        print(f"[MCP] {spec.alias}: соединение с {display_names[spec.alias]}, {len(names)} инструмент(а)")

    return registry, openai_tools, display_names


def print_registry(registry: dict[str, Route], display_names: dict[str, str]) -> None:
    """Печатает реестр «сервер → инструменты»: это карта маршрутизации."""
    print(f"\n[РЕЕСТР] {len(registry)} инструмент(ов) из {len(SERVERS)} серверов:")
    for spec in SERVERS:
        tools = [name.split(TOOL_PREFIX_SEP, 1)[1] for name in registry if registry[name].alias == spec.alias]
        print(f"  {spec.alias:<9} ({display_names[spec.alias]}): {', '.join(tools)}")
        print(f"            → {spec.about}")


# --------------------------------------------------------------------------- #
# Агентный цикл с МАРШРУТИЗАЦИЕЙ: выбор инструмента — за LLM, доставка —
# по реестру в сессию нужного сервера.
# --------------------------------------------------------------------------- #


async def ask_agent(
    registry: dict[str, Route],
    routing_stats: Counter,
    http: httpx.AsyncClient,
    cfg: dict[str, str],
    openai_tools: list[dict[str, Any]],
    question: str,
) -> None:
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": question},
    ]
    print(f"\n[USER] {question}")

    for round_no in range(1, MAX_TOOL_ROUNDS + 1):
        message = await chat_completion(
            http, cfg["endpoint"], cfg["api_key"], cfg["model"], messages, openai_tools
        )
        tool_calls = message.get("tool_calls") or []

        if not tool_calls:  # модель закончила — это финальный ответ
            print(f"\n[ASSISTANT] {message.get('content') or '(пустой ответ)'}")
            return

        messages.append(
            {
                "role": "assistant",
                "content": message.get("content") or "",
                "tool_calls": tool_calls,
            }
        )
        for call in tool_calls:
            full_name = call["function"]["name"]
            args = parse_tool_args(call["function"].get("arguments"))

            route = registry.get(full_name)
            if route is None:  # модель выдумала имя — сообщаем ей это и даём исправиться
                text = f"ОШИБКА МАРШРУТИЗАЦИИ: инструмент «{full_name}» не зарегистрирован. Доступные префиксы: " + ", ".join(
                    sorted({r.alias for r in registry.values()})
                )
                print(f"[РАУНД {round_no}] [!] {text}")
                messages.append({"role": "tool", "tool_call_id": call.get("id", ""), "content": text})
                continue

            # Собственно маршрутизация: вызов уходит в сессию нужного сервера.
            print(f"[РАУНД {round_no}] LLM → MCP[{route.alias}]: call_tool {route.tool_name}({json.dumps(args, ensure_ascii=False)})")
            result = await route.session.call_tool(route.tool_name, args)
            text = tool_result_text(result)
            print(f"[РАУНД {round_no}] MCP[{route.alias}] → LLM: {preview(text)}")

            routing_stats[route.alias] += 1
            messages.append({"role": "tool", "tool_call_id": call.get("id", ""), "content": text})

    print("\n[!] Достигнут лимит раундов вызова инструментов — прерываю цикл.")


def demo_header(title: str) -> None:
    print("\n" + "=" * 72)
    print(f"[ДЕМО] {title}")
    print("=" * 72)


# --------------------------------------------------------------------------- #
# Smoke-режим: прямой вызов одного инструмента на каждом сервере (без LLM).
# --------------------------------------------------------------------------- #


async def run_smoke(registry: dict[str, Route]) -> bool:
    print("\n[SMOKE] Прямая проверка серверов без LLM (маршрутизация по реестру):")
    ok = True
    for alias, tool_name, args in SMOKE_CALLS:
        full_name = f"{alias}{TOOL_PREFIX_SEP}{tool_name}"
        route = registry.get(full_name)
        if route is None:
            print(f"  [FAIL] {full_name}: нет в реестре")
            ok = False
            continue
        result = await route.session.call_tool(route.tool_name, args)
        text = tool_result_text(result)
        is_error = text.startswith("ОШИБКА ИНСТРУМЕНТА")
        status = "FAIL" if is_error else "OK"
        ok = ok and not is_error
        print(f"  [{status}] {full_name} → {preview(text, 200)}")
    return ok


# --------------------------------------------------------------------------- #
# Приложение.
# --------------------------------------------------------------------------- #


async def main() -> int:
    load_dotenv()
    api_key = os.getenv("ZAI_API_KEY", "")
    if not api_key or api_key == "your_api_key_here":
        print("[ОШИБКА] ZAI_API_KEY не задан: скопируй .env.example в .env и впиши ключ.")
        return 1

    argv = [a for a in sys.argv[1:] if a != "--smoke"]
    smoke_mode = "--smoke" in sys.argv
    custom_question = " ".join(argv).strip()

    cfg = {
        "api_key": api_key,
        "endpoint": os.getenv("ZAI_ENDPOINT", DEFAULT_ZAI_ENDPOINT),
        "model": os.getenv("ZAI_MODEL", DEFAULT_MODEL),
    }

    print("Week 4 · Day 5 — MCP Orchestration: мульти-серверный пайплайн")
    print(f"Модель агента: {cfg['model']} ({cfg['endpoint']})")

    routing_stats: Counter = Counter()

    # AsyncExitStack держит ВСЕ 4 транспорта и сессии открытыми до конца main:
    # без вложенных async with на каждую пару read/write.
    async with AsyncExitStack() as stack:
        registry, openai_tools, display_names = await connect_servers(stack)
        print_registry(registry, display_names)

        if smoke_mode:
            ok = await run_smoke(registry)
            print("\n[SMOKE] Все серверы отвечают." if ok else "\n[SMOKE] Есть ошибки — см. выше.")
            # Код выхода возвращаем, а не бросаем sys.exit прямо здесь:
            # SystemExit внутри async with ломает чистое закрытие MCP-сессий
            # (anyio оборачивает его в ExceptionGroup на размотке стека).
            return 0 if ok else 1

        async with httpx.AsyncClient(timeout=120.0) as http:
            if custom_question:
                await ask_agent(registry, routing_stats, http, cfg, openai_tools, custom_question)
            else:
                # ДЕМО 1 — длинный сценарий: все 4 сервера в одной задаче,
                # данные перетекают между серверами через агента.
                demo_header("1 · Длинный сценарий: поездка в Сочи силами 4 серверов")
                await ask_agent(
                    registry, routing_stats, http, cfg, openai_tools,
                    "Спланируй мою поездку в Сочи на эти выходные, задействовав все четыре "
                    "сервера. План: 1) weather — найди город и возьми прогноз на 7 дней; "
                    "2) journal — поставь Сочи под наблюдение и сделай немедленный замер; "
                    "3) pipeline — прогони конвейер «получение → обработка → сохранение» "
                    "для Сочи и проверь цепочку аудитом; 4) travel — оцени погоду для "
                    "поездки, посоветуй одежду по худшему дню и собери чек-лист на 2 дня. "
                    "В финале: стоит ли ехать, что взять, и перечисли все идентификаторы "
                    "(наблюдение, артефакты, отчёт, run_id).",
                )

                # ДЕМО 2 — свободный выбор: подсказок, каким сервером пользоваться, нет;
                # модель сама маршрутизирует сравнение двух городов и совет по одежде.
                demo_header("2 · Свободный выбор инструментов: сравнение городов без подсказок")
                await ask_agent(
                    registry, routing_stats, http, cfg, openai_tools,
                    "Сравни текущую погоду в Санкт-Петербурге и Сочи. Для более тёплого "
                    "города прогони конвейер анализа с сохранением отчёта, проверь его "
                    "аудитом и посоветуй одежду на завтрашнюю прогулку по текущей погоде "
                    "там. Города под наблюдение не ставь.",
                )

    # Итог маршрутизации: сколько вызовов ушло на каждый сервер.
    print("\n" + "=" * 72)
    print("[МАРШРУТИЗАЦИЯ] вызовы по серверам за прогон:")
    for spec in SERVERS:
        print(f"  {spec.alias:<9} {routing_stats.get(spec.alias, 0)} вызов(а/ов)")
    print(f"  всего: {sum(routing_stats.values())}")

    print("\nАртефакты: week-4/day-5/reports/report_*.json, pipeline.db, weather_journal.db.")
    print("Готово.")
    return 0


if __name__ == "__main__":
    # Консоль Windows по умолчанию не UTF-8 — выводим кириллицу корректно.
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    sys.exit(asyncio.run(main()))
