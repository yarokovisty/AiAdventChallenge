"""
Week 4 · Day 3 — Приложение: агент + MCP-сервер с отложенным/периодическим
выполнением.

Задание: сохранять данные в JSON или SQLite, выполнять работу по расписанию
и возвращать агрегированный результат.

Приложение связывает три роли (ядро клиента и мост MCP ⇄ OpenAI tool-calling
переиспользованы из day-2):

  1. MCP-клиент   — запускает mcp_weather_journal_server.py как подпроцесс
                    с КОРОТКИМ интервалом планировщика (демо: 15 с вместо
                    боевых 10 минут) и держит сессию открытой;
  2. Агент        — LLM GLM (Z.ai) с tool-calling: инструменты журнала
                    конвертируются в OpenAI-формат tools;
  3. Демонстрация — сценарий «отложенное → периодическое → агрегат»:

        шаг 1  агент ставит город под наблюдение (watch_city: замер отложен);
        пауза  приложение НЕ закрывает сессию и ждёт: в это время фоновый
               планировщик сервера по тикам опрашивает Open-Meteo и пишет
               замеры в SQLite (видно по логам [планировщик] в stderr);
        шаг 2  агент запрашивает агрегированную статистику (get_weather_stats)
               и пересказывает её человеку.

Запуск:
    python agent_app.py                # демо-сценарий (наблюдение → пауза → агрегат)
    python agent_app.py "ваш вопрос"   # свой вопрос агенту журнала
"""

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# OpenAI-совместимый coding-эндпоинт Z.ai (тот же, что в AgentDemo и day-2).
DEFAULT_ZAI_ENDPOINT = "https://api.z.ai/api/coding/paas/v4/chat/completions"
DEFAULT_MODEL = "glm-4.6"
MAX_TOOL_ROUNDS = 5  # страховка от бесконечного цикла вызовов

SERVER_SCRIPT = Path(__file__).with_name("mcp_weather_journal_server.py")

# Демо-настройки расписания: интервал планировщика сервера и сколько ждать
# накопления замеров перед вопросом об агрегате (3 тика по 15 с).
DEMO_INTERVAL_SEC = 15.0
DEMO_WAIT_SEC = 45.0

SYSTEM_PROMPT = (
    "Ты — ассистент метеожурнала. Журнал ведёт MCP-сервер: он наблюдает за "
    "городами и по расписанию копит замеры погоды в SQLite. Отвечай по-русски, "
    "кратко и по делу. Инструменты: watch_city — поставить город под наблюдение "
    "(первый замер отложен до тика планировщика); collect_now — форсировать "
    "замер немедленно; list_watches — что наблюдается и сколько замеров "
    "накоплено; get_weather_stats — агрегированная статистика по городу "
    "(средняя/мин/макс температура, осадки, тренд); unwatch_city — снять "
    "наблюдение. Для нового города используй watch_city, для анализа "
    "накопленного — get_weather_stats; в ответе называй число замеров, окно "
    "времени и тренд."
)


# --------------------------------------------------------------------------- #
# Транспорт к LLM (Z.ai GLM, OpenAI-совместимый протокол) — как в day-2.
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
        "thinking": {"type": "disabled"},  # журнальному агенту размышления не нужны
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
# Мост MCP ⇄ OpenAI tool-calling — переиспользован из day-2.
# --------------------------------------------------------------------------- #


def tool_schema(tool: Any, attr: str) -> dict[str, Any] | None:
    """Достаёт схему инструмента, переживая различия имён полей в SDK."""
    value = getattr(tool, attr, None)
    return value or None


def mcp_tool_to_openai(tool: Any) -> dict[str, Any]:
    """MCP-инструмент (имя, описание, inputSchema) → формат tools OpenAI."""
    input_schema = tool_schema(tool, "input_schema") or tool_schema(tool, "inputSchema") or {}
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description or "",
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


def preview(text: str, limit: int = 320) -> str:
    return text if len(text) <= limit else text[:limit] + " …"


# --------------------------------------------------------------------------- #
# Агентный цикл (один вопрос → раунды tool-calling → финальный ответ).
# --------------------------------------------------------------------------- #


async def ask_agent(
    session: ClientSession,
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
            name = call["function"]["name"]
            args = parse_tool_args(call["function"].get("arguments"))
            print(f"[РАУНД {round_no}] LLM → MCP: call_tool {name}({json.dumps(args, ensure_ascii=False)})")

            result = await session.call_tool(name, args)
            text = tool_result_text(result)
            print(f"[РАУНД {round_no}] MCP → LLM: {preview(text)}")

            messages.append({"role": "tool", "tool_call_id": call.get("id", ""), "content": text})

    print("\n[!] Достигнут лимит раундов вызова инструментов — прерываю цикл.")


async def wait_for_scheduler(seconds: float, interval: float) -> None:
    """Пауза демо: сессия жива, фоновый планировщик сервера копит замеры."""
    ticks = int(seconds / interval)
    print(
        f"\n[ПАУЗА] {seconds:g} с держим MCP-сессию открытой: фоновый планировщик "
        f"сервера делает ~{ticks} замер(а) с интервалом {interval:g} с "
        "(его тики видны ниже в логах [планировщик])…"
    )
    for done in range(1, ticks + 1):
        await asyncio.sleep(interval)
        print(f"[ПАУЗА] {done}/{ticks} тик(ов) прошло…")
    print("[ПАУЗА] замеры накоплены в SQLite — спрашиваем агрегат.")


# --------------------------------------------------------------------------- #
# Приложение: поднимает сервер с демо-интервалом, крутит сценарий.
# --------------------------------------------------------------------------- #


async def main() -> None:
    load_dotenv()
    api_key = os.getenv("ZAI_API_KEY", "")
    if not api_key or api_key == "your_api_key_here":
        print("[ОШИБКА] ZAI_API_KEY не задан: скопируй .env.example в .env и впиши ключ.")
        sys.exit(1)

    cfg = {
        "api_key": api_key,
        "endpoint": os.getenv("ZAI_ENDPOINT", DEFAULT_ZAI_ENDPOINT),
        "model": os.getenv("ZAI_MODEL", DEFAULT_MODEL),
    }
    custom_question = " ".join(sys.argv[1:]).strip()

    print("Week 4 · Day 3 — MCP с отложенным/периодическим выполнением: метеожурнал")
    print(f"Модель агента: {cfg['model']} ({cfg['endpoint']})")

    # Наш MCP-сервер запускается как подпроцесс тем же интерпретатором Python.
    # Демо-интервал планировщика передан через окружение: 15 с вместо дефолтных
    # 600 с — иначе «периодичность» на демонстрации ждать слишком долго.
    interval = float(os.getenv("JOURNAL_INTERVAL_SEC", DEMO_INTERVAL_SEC))
    server_params = StdioServerParameters(
        command=sys.executable,
        args=[str(SERVER_SCRIPT)],
        env={**os.environ, "JOURNAL_INTERVAL_SEC": str(interval)},
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            print(f"[MCP] initialize → соединение с {init.server_info.name} v{init.server_info.version}")

            tools_result = await session.list_tools()
            mcp_tools = tools_result.tools
            openai_tools = [mcp_tool_to_openai(t) for t in mcp_tools]
            print(f"[MCP] tools/list → {len(mcp_tools)} инструмент(а): " + ", ".join(t.name for t in mcp_tools))

            async with httpx.AsyncClient(timeout=120.0) as http:
                if custom_question:
                    await ask_agent(session, http, cfg, openai_tools, custom_question)
                else:
                    # Демо-сценарий: отложенное выполнение → периодический сбор → агрегат.
                    await ask_agent(
                        session, http, cfg, openai_tools,
                        "Поставь Санкт-Петербург под наблюдение и объясни, "
                        "когда появятся первые замеры.",
                    )
                    await wait_for_scheduler(DEMO_WAIT_SEC, interval)
                    await ask_agent(
                        session, http, cfg, openai_tools,
                        "Покажи агрегированную статистику журнала по Санкт-Петербургу: "
                        "сколько замеров накоплено, какая средняя температура и тренд.",
                    )

    print("\nГотово.")


if __name__ == "__main__":
    # Консоль Windows по умолчанию не UTF-8 — выводим кириллицу корректно.
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    asyncio.run(main())
