"""
Week 4 · Day 2 — Приложение: агент, у которого инструмент — MCP-инструмент.

Задание: подключить инструмент к агенту и показать вызов из приложения.

Приложение связывает три роли:

  1. MCP-клиент  — запускает наш сервер (mcp_weather_server.py) как подпроцесс,
                   делает initialize и tools/list, умеет вызывать инструменты
                   через session.call_tool(...);
  2. Агент       — LLM GLM (Z.ai) в агентном цикле с tool-calling'ом: описание
                   MCP-инструментов (inputSchema) конвертируется в OpenAI-формат
                   tools; когда модель решает вызвать инструмент, приложение
                   исполняет вызов через MCP и возвращает результат модели;
  3. Демонстрация — печатает трассировку: какие инструменты агент вызвал,
                   с какими аргументами и что сервер вернул.

Цикл агента (агентный loop):

    user ─► LLM (GLM, tools=[geocode_city, get_weather_forecast])
              │ tool_calls?
              ├── да ─► MCP call_tool ─► результат ─► назад в LLM (раунд 2, 3, …)
              └── нет ─► финальный ответ пользователю

Запуск:
    python agent_app.py                       # демо-вопрос о погоде
    python agent_app.py "ваш вопрос"          # свой вопрос
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

# OpenAI-совместимый coding-эндпоинт Z.ai (тот же, что в AgentDemo недель 2–3).
DEFAULT_ZAI_ENDPOINT = "https://api.z.ai/api/coding/paas/v4/chat/completions"
DEFAULT_MODEL = "glm-4.6"
MAX_TOOL_ROUNDS = 5  # страховка от бесконечного цикла вызовов

SERVER_SCRIPT = Path(__file__).with_name("mcp_weather_server.py")

DEFAULT_QUESTION = "Какая сейчас погода в Санкт-Петербурге и стоит ли брать зонт?"

SYSTEM_PROMPT = (
    "Ты — ассистент по погоде. Отвечай по-русски, кратко и по делу. "
    "Для любых вопросов о погоде используй инструменты: сначала узнай координаты "
    "города через geocode_city, затем получи погоду через get_weather_forecast. "
    "В ответе назови текущую температуру и ощущаемую, осадки и ветер и дай "
    "короткий практический совет (зонт, одежда)."
)


# --------------------------------------------------------------------------- #
# Транспорт к LLM (Z.ai GLM, OpenAI-совместимый протокол).
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
        "thinking": {"type": "disabled"},  # погодному агенту размышления не нужны
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
# Мост MCP ⇄ OpenAI tool-calling.
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
    """Результат call_tool → текст для сообщения role=tool.

    Приоритет — structuredContent (машинный JSON по outputSchema);
    при ошибке текст ошибки возвращается модели, чтобы она могла исправить вызов.
    """
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


def preview(text: str, limit: int = 260) -> str:
    return text if len(text) <= limit else text[:limit] + " …"


# --------------------------------------------------------------------------- #
# Агентный цикл.
# --------------------------------------------------------------------------- #


async def run_agent(session: ClientSession, http: httpx.AsyncClient, cfg: dict[str, str], question: str) -> None:
    tools_result = await session.list_tools()
    mcp_tools = tools_result.tools
    openai_tools = [mcp_tool_to_openai(t) for t in mcp_tools]

    print(f"[MCP] tools/list → {len(mcp_tools)} инструмент(а): " + ", ".join(t.name for t in mcp_tools))

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

        # Модель просит вызвать инструменты — исполняем каждый через MCP.
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
            print(f"\n[РАУНД {round_no}] LLM → MCP: call_tool {name}({json.dumps(args, ensure_ascii=False)})")

            result = await session.call_tool(name, args)
            text = tool_result_text(result)
            print(f"[РАУНД {round_no}] MCP → LLM: {preview(text)}")

            messages.append({"role": "tool", "tool_call_id": call.get("id", ""), "content": text})

    print("\n[!] Достигнут лимит раундов вызова инструментов — прерываю цикл.")


# --------------------------------------------------------------------------- #
# Приложение: поднимает MCP-сервер, подключает агента, показывает вызов.
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
    question = " ".join(sys.argv[1:]).strip() or DEFAULT_QUESTION

    print("Week 4 · Day 2 — свой MCP-сервер + агент, вызов инструмента из приложения")
    print(f"Модель агента: {cfg['model']} ({cfg['endpoint']})")

    # Наш MCP-сервер запускается как подпроцесс тем же интерпретатором Python.
    server_params = StdioServerParameters(
        command=sys.executable,
        args=[str(SERVER_SCRIPT)],
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            print(f"[MCP] initialize → соединение с {init.server_info.name} v{init.server_info.version}")

            async with httpx.AsyncClient(timeout=120.0) as http:
                await run_agent(session, http, cfg, question)

    print("\nГотово.")


if __name__ == "__main__":
    # Консоль Windows по умолчанию не UTF-8 — выводим кириллицу корректно.
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    asyncio.run(main())
