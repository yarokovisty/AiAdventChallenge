"""
Week 4 · Day 1 — Минимальный MCP-клиент: подключение и список инструментов.

Задание: установить MCP SDK, написать минимальный код, который
  1) устанавливает MCP-соединение,
  2) получает от MCP список доступных инструментов,
и проверить, что соединение встаёт, а список инструментов корректно возвращается.

Скрипт подключается к двум MCP-серверам:
  • Локальный reference-сервер "everything" по транспорту stdio (через npx) —
    работает всегда, без подписки и без ключей.
  • Remote MCP-сервер Z.ai (web_search_prime) по транспорту streamable HTTP —
    по теме курса; требует ключ ZAI_API_KEY и активный GLM Coding Plan.

Запуск:
    python mcp_list_tools.py
"""

import asyncio
import os
import shutil
import sys

from dotenv import load_dotenv

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

# Endpoint remote MCP-сервера Z.ai (Web Search). Можно переопределить через .env.
DEFAULT_ZAI_MCP_URL = "https://api.z.ai/api/mcp/web_search_prime/mcp"


def print_tools(server_name: str, tools) -> None:
    """Печатает человекочитаемый список инструментов, полученный из tools/list."""
    print(f"  Инструментов получено: {len(tools)}")
    for i, tool in enumerate(tools, start=1):
        description = (tool.description or "").strip().replace("\n", " ")
        if len(description) > 100:
            description = description[:97] + "..."
        print(f"    {i}. {tool.name} — {description or '(без описания)'}")


async def list_tools_stdio() -> None:
    """Подключается к локальному reference-серверу по stdio и печатает его инструменты."""
    print("\n" + "=" * 70)
    print("[1] Локальный reference-сервер MCP (транспорт: stdio)")
    print("=" * 70)

    # На Windows npx — это npx.cmd; shutil.which вернёт полный путь с нужным расширением.
    npx_path = shutil.which("npx")
    if npx_path is None:
        print("  [ПРОПУСК] npx не найден в PATH — нужен Node.js, чтобы запустить")
        print("            локальный сервер (npx -y @modelcontextprotocol/server-everything).")
        return

    server_params = StdioServerParameters(
        command=npx_path,
        args=["-y", "@modelcontextprotocol/server-everything"],
    )

    try:
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                # Шаг 1: установить MCP-соединение (handshake initialize).
                init_result = await session.initialize()
                server_info = init_result.server_info
                print(f"  [OK] Соединение установлено: {server_info.name} v{server_info.version}")

                # Шаг 2: получить список доступных инструментов.
                tools_result = await session.list_tools()
                print_tools("everything", tools_result.tools)
    except Exception as exc:  # noqa: BLE001 — демо: показываем любую причину сбоя
        print(f"  [ОШИБКА] Не удалось подключиться к локальному серверу: {exc!r}")


async def list_tools_zai() -> None:
    """Подключается к remote MCP-серверу Z.ai по streamable HTTP и печатает его инструменты."""
    print("\n" + "=" * 70)
    print("[2] Remote MCP-сервер Z.ai · web_search_prime (транспорт: streamable HTTP)")
    print("=" * 70)

    api_key = os.getenv("ZAI_API_KEY")
    url = os.getenv("ZAI_MCP_URL", DEFAULT_ZAI_MCP_URL)

    if not api_key or api_key == "your_api_key_here":
        print("  [ПРОПУСК] Переменная ZAI_API_KEY не задана.")
        print("            Скопируй .env.example в .env и впиши свой ключ Z.ai,")
        print("            чтобы проверить подключение к remote MCP-серверу.")
        return

    headers = {"Authorization": f"Bearer {api_key}"}

    try:
        # В MCP SDK 2.x заголовки (в т.ч. авторизацию) задают через httpx-клиент,
        # который затем передаётся в транспорт streamable HTTP.
        async with create_mcp_http_client(headers=headers) as http_client:
            async with streamable_http_client(url, http_client=http_client) as (read, write):
                async with ClientSession(read, write) as session:
                    # Шаг 1: установить MCP-соединение.
                    init_result = await session.initialize()
                    server_info = init_result.server_info
                    print(f"  [OK] Соединение установлено: {server_info.name} v{server_info.version}")

                    # Шаг 2: получить список доступных инструментов.
                    tools_result = await session.list_tools()
                    print_tools("z.ai", tools_result.tools)
    except Exception as exc:  # noqa: BLE001
        print(f"  [ОШИБКА] Не удалось подключиться к Z.ai MCP: {exc!r}")
        print("           Частые причины: нет активного GLM Coding Plan или неверный ключ.")


async def main() -> None:
    load_dotenv()
    print("Week 4 · Day 1 — проверка MCP-соединения и получение списка инструментов")
    await list_tools_stdio()
    await list_tools_zai()
    print("\nГотово.")


if __name__ == "__main__":
    # Консоль Windows по умолчанию не UTF-8 — выводим кириллицу корректно.
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    # На Windows для stdio-подпроцессов нужен ProactorEventLoop — в Python 3.8+ он по умолчанию.
    asyncio.run(main())
