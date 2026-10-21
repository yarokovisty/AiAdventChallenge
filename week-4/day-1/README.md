# Week 4 · Day 1 — Подключение к MCP и список инструментов

## Условие задания

Установить MCP SDK / клиент (или поднять MCP-сервер, если используется локальный
вариант). Сделать минимальный код, который:

- устанавливает **MCP-соединение**;
- получает от MCP **список доступных инструментов**.

Проверить:

- соединение **устанавливается**;
- список инструментов **корректно возвращается**.

**Результат:** код, который подключается к MCP и выводит список доступных инструментов.

---

## Смена стека: почему Python, а не Android

Недели 2–3 делались как Android-приложение `AgentDemo` (Kotlin, Clean + MVI, Koin).
Для первого дня недели 4 задача — **минимальный клиент MCP**, поэтому взят отдельный
standalone-скрипт на **Python** с официальным пакетом [`mcp`](https://pypi.org/project/mcp/):
это самый короткий и наглядный путь к «подключиться + получить tools/list», не
затрагивающий Android-проект. Интеграцию MCP в самого агента логично оставить на
следующие дни недели.

---

## Что сделано

Скрипт [`mcp_list_tools.py`](mcp_list_tools.py) подключается к **двум** MCP-серверам
и для каждого делает ровно то, что требует задание — `initialize` (соединение) и
`tools/list` (список инструментов):

| # | Сервер | Транспорт | Доступность |
|---|--------|-----------|-------------|
| 1 | Локальный reference-сервер `@modelcontextprotocol/server-everything` | **stdio** (через `npx`) | всегда, без ключей и подписки |
| 2 | Remote MCP Z.ai — `web_search_prime` | **streamable HTTP** | нужен ключ `ZAI_API_KEY` + GLM Coding Plan |

Два сервера выбраны намеренно: Z.ai — **по теме курса** (все прошлые недели работали
через Z.ai/GLM), а локальный reference-сервер — **страховка**: задание проверяемо
даже без активного Coding Plan. Если ключа нет или план неактивен, ветка Z.ai
аккуратно пропускается/сообщает причину, а локальная ветка всё равно доказывает, что
соединение встаёт и список инструментов возвращается.

### Минимальное ядро клиента

Обе ветки — это один и тот же паттерн MCP-клиента, меняется только транспорт:

```python
# stdio — локальный сервер запускается как подпроцесс
async with stdio_client(server_params) as (read, write):
    async with ClientSession(read, write) as session:
        init_result = await session.initialize()          # 1) соединение (handshake)
        tools_result = await session.list_tools()         # 2) список инструментов

# streamable HTTP — remote-сервер Z.ai, авторизация через httpx-клиент
async with create_mcp_http_client(headers={"Authorization": f"Bearer {key}"}) as http:
    async with streamable_http_client(url, http_client=http) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            await session.list_tools()
```

---

## Детали реализации (MCP Python SDK 2.x)

В проекте установился `mcp` **2.3.0**, где API заметно отличается от примеров 1.x —
пришлось сверяться с исходниками пакета:

- функция транспорта называется `streamable_http_client` (в 1.x было
  `streamablehttp_client`) и **не принимает `headers`** напрямую — заголовки
  (в т.ч. `Authorization`) задаются через `create_mcp_http_client(headers=...)`,
  который отдаёт `httpx`-клиент; он передаётся в транспорт параметром `http_client`;
- транспорт отдаёт кортеж `(read, write)` (без `get_session_id`, как было в 1.x);
- у `InitializeResult` поле называется `server_info` (snake_case), а не `serverInfo`.

Windows-специфика:

- `npx` резолвится через `shutil.which("npx")` — иначе на Windows не находится
  (реально это `npx.cmd`);
- для stdio нужен `ProactorEventLoop` — в Python 3.8+ он уже по умолчанию;
- консоль перенастраивается на UTF-8 (`sys.stdout.reconfigure`), иначе кириллица
  выводится «кракозябрами».

---

## Как запустить

```bash
cd week-4/day-1
python -m venv venv
venv\Scripts\python.exe -m pip install -r requirements.txt   # Windows
python mcp_list_tools.py
```

Для проверки ветки Z.ai: скопировать `.env.example` → `.env` и вписать `ZAI_API_KEY`.
Локальной ветке нужен установленный Node.js (используется `npx`).

---

## Живой прогон

Окружение: Python 3.14.2, Node.js 24, MCP SDK `mcp` 2.3.0 (Windows).

```
Week 4 · Day 1 — проверка MCP-соединения и получение списка инструментов

======================================================================
[1] Локальный reference-сервер MCP (транспорт: stdio)
======================================================================
  [OK] Соединение установлено: mcp-servers/everything v1.0.0
  Инструментов получено: 13
    1. echo — Echoes back the input string
    2. get-annotated-message — Demonstrates how annotations can be used ...
    3. get-env — Returns all environment variables, helpful for debugging ...
    4. get-resource-links — Returns up to ten resource links ...
    5. get-resource-reference — Returns a resource reference ...
    6. get-structured-content — Returns structured content along with an output schema ...
    7. get-sum — Returns the sum of two numbers
    8. get-tiny-image — Returns a tiny MCP logo image.
    9. gzip-file-as-resource — Compresses a single file using gzip ...
    10. toggle-simulated-logging — Toggles simulated, random-leveled logging ...
    11. toggle-subscriber-updates — Toggles simulated resource subscription updates ...
    12. trigger-long-running-operation — Demonstrates a long running operation ...
    13. simulate-research-query — Simulates a deep research operation ...

======================================================================
[2] Remote MCP-сервер Z.ai · web_search_prime (транспорт: streamable HTTP)
======================================================================
  [ПРОПУСК] Переменная ZAI_API_KEY не задана.
            ...

Готово.
```

**Критерии задания выполнены:** соединение устанавливается (`[OK] Соединение
установлено`), список инструментов возвращается корректно (13 инструментов локального
сервера с именами и описаниями). Ветка Z.ai подключается тем же кодом по streamable
HTTP, как только задан `ZAI_API_KEY` с активным GLM Coding Plan.

---

## Выводы

- **MCP-клиент — это три шага независимо от транспорта**: открыть транспорт →
  `ClientSession` → `initialize` + `list_tools`. Меняется только способ соединения
  (stdio-подпроцесс против streamable HTTP с Bearer-токеном).
- **Версии SDK ломают API**: примеры из интернета были под 1.x, реальный 2.3.0
  потребовал `streamable_http_client` + `create_mcp_http_client` и `server_info`.
  Полезный урок — сверяться с установленной версией, а не с туториалами.
- **Две цели = устойчивая проверка**: локальный reference-сервер гарантирует, что
  задание зачтётся даже без Coding Plan, а ветка Z.ai держит связь с темой курса.
