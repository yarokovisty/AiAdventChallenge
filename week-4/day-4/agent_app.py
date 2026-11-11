"""
Week 4 · Day 4 — Приложение: агент + автоматический конвейер MCP-инструментов.

Задание: создать несколько MCP-инструментов и автоматическую цепочку
«получение данных → обработка → сохранение»; проверить выполнение цепочки
и передачу данных.

Приложение связывает три роли (ядро клиента и мост MCP ⇄ OpenAI tool-calling
переиспользованы из day-2/day-3):

  1. MCP-клиент — запускает mcp_pipeline_server.py как подпроцесс и получает
     6 инструментов конвейера;
  2. Агент      — LLM GLM (Z.ai) с tool-calling;
  3. Демонстрация КОМПОЗИЦИИ в двух режимах:

     ДЕМО 1 (авто):  агент запускает run_pipeline — сервер сам гоняет стадии
                     fetch → analyze → save, передавая artifact_id между ними, —
                     и тут же проверяет цепочку инструментом inspect_run;
     ДЕМО 2 (ручная): агент САМ выступает оркестратором: вызывает стадии по
                     одной и передаёт artifact_id от инструмента к инструменту
                     (передача данных видна в трассировке раундов).

Запуск:
    python agent_app.py                # демо: автоцепочка + ручная цепочка
    python agent_app.py "ваш вопрос"   # свой вопрос агенту конвейера
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

# OpenAI-совместимый coding-эндпоинт Z.ai (тот же, что в AgentDemo и day-2/3).
DEFAULT_ZAI_ENDPOINT = "https://api.z.ai/api/coding/paas/v4/chat/completions"
DEFAULT_MODEL = "glm-4.6"
MAX_TOOL_ROUNDS = 8  # ручная цепочка — до 3 вызовов + аудит; запас на повторы

SERVER_SCRIPT = Path(__file__).with_name("mcp_pipeline_server.py")

SYSTEM_PROMPT = (
    "Ты — ассистент конвейера данных «получение → обработка → сохранение». "
    "MCP-сервер хранит данные стадий как артефакты в SQLite. Инструменты: "
    "fetch_weather — стадия «получение данных» (Open-Meteo, 7 дней; возвращает "
    "artifact_id и payload_hash); analyze_data — стадия «обработка» (агрегаты + "
    "текстовый вывод GLM или по правилам; принимает artifact_id от fetch_weather); "
    "save_report — стадия «сохранение» (SQLite + JSON-файл; принимает artifact_id "
    "от analyze_data); run_pipeline — прогнать все три стадии автоматически одним "
    "вызовом; inspect_run — аудит запуска: выполнение цепочки и передача данных "
    "(порядок стадий, связность ссылок, совпадение хэшей, файл отчёта); "
    "list_reports — сохранённые отчёты. Отвечай по-русски и кратко. В ответе "
    "называй идентификаторы (artifact_id, report_id, run_id) — по ним видно "
    "передачу данных между стадиями."
)


# --------------------------------------------------------------------------- #
# Транспорт к LLM (Z.ai GLM, OpenAI-совместимый протокол) — как в day-2/3.
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
        "thinking": {"type": "disabled"},  # конвейерному агенту размышления не нужны
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
# Мост MCP ⇄ OpenAI tool-calling — переиспользован из day-2/3.
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


def preview(text: str, limit: int = 340) -> str:
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


def demo_header(title: str) -> None:
    print("\n" + "=" * 72)
    print(f"[ДЕМО] {title}")
    print("=" * 72)


# --------------------------------------------------------------------------- #
# Приложение: поднимает сервер конвейера, крутит два демо композиции.
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

    print("Week 4 · Day 4 — MCP Composition: автоматический конвейер данных")
    print(f"Модель агента: {cfg['model']} ({cfg['endpoint']})")

    # Наш MCP-сервер запускается как подпроцесс тем же интерпретатором Python.
    # Окружение передаём целиком: серверу нужен ZAI_API_KEY для LLM-анализа
    # на стадии «обработка» (нет ключа или сбой → вывод по правилам).
    server_params = StdioServerParameters(
        command=sys.executable,
        args=[str(SERVER_SCRIPT)],
        env={**os.environ},
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
                    # ДЕМО 1 — серверная композиция: автоцепочка одним вызовом
                    # + проверка выполнения и передачи данных.
                    demo_header("1 · Автоматическая цепочка: run_pipeline → inspect_run")
                    await ask_agent(
                        session, http, cfg, openai_tools,
                        "Прогони автоматический конвейер для города Санкт-Петербург: "
                        "получение данных → обработка → сохранение. Затем проверь выполнение "
                        "цепочки и передачу данных по run_id и перескажи результаты проверок.",
                    )

                    # ДЕМО 2 — агентная композиция: оркестратор — сама LLM,
                    # она передаёт artifact_id между стадиями.
                    demo_header("2 · Ручная цепочка агентом: fetch_weather → analyze_data → save_report")
                    await ask_agent(
                        session, http, cfg, openai_tools,
                        "Теперь собери цепочку вручную из отдельных стадий для города Сочи: "
                        "получи данные, обработай их и сохрани отчёт. Передавай artifact_id от "
                        "инструмента к инструменту сам, без run_pipeline. В конце назови все "
                        "идентификаторы цепочки (артефакты и отчёт).",
                    )

    print("\nОтчёты конвейера: week-4/day-4/reports/report_*.json (и SQLite pipeline.db).")
    print("Готово.")


if __name__ == "__main__":
    # Консоль Windows по умолчанию не UTF-8 — выводим кириллицу корректно.
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    asyncio.run(main())
