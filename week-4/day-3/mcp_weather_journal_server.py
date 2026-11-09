"""
Week 4 · Day 3 — MCP-сервер с отложенным и периодическим выполнением.

Задание: сделать MCP-инструмент с отложенным или периодическим выполнением —
сохранять данные в JSON или SQLite, выполнять работу по расписанию и
возвращать агрегированный результат.

Этот файл — MCP-СЕРВЕР (транспорт stdio) «метеожурнал»: он не отвечает на
«какая погода сейчас» (это было в day-2), а ведёт ЖУРНАЛ наблюдений:

  • watch_city      — ставит город под наблюдение (ОТЛОЖЕННОЕ выполнение:
                      сам вызов ничего не измеряет, замеры начнёт планировщик
                      на ближайшем тике);
  • unwatch_city    — снимает город с наблюдения (данные сохраняются);
  • list_watches    — что наблюдаем, сколько замеров накоплено, последний замер;
  • collect_now     — форсирует сбор по всем городам немедленно, не дожидаясь
                      тика планировщика;
  • get_weather_stats — АГРЕГИРОВАННЫЙ результат за окно времени: средняя/
                      мин/макс температура (SQL AVG/MIN/MAX), суммарные осадки,
                      максимальный ветер, доминирующая погода и тренд.

Как устроено «расписание». MCP — протокол запрос/ответ: сервер не может сам
«протолкнуть» данные клиенту. Поэтому периодическая работа живёт ФОНОВОЙ
asyncio-задачей внутри сервера: она стартует и останавливается вместе с
сервером через lifespan, каждые JOURNAL_INTERVAL_SEC секунд опрашивает
Open-Meteo по всем городам из таблицы watches и дописывает замеры в
observations. Клиент в любой момент читает накопленное инструментами.

Хранение — SQLite (weather_journal.db рядом со скриптом): стандартная
библиотека, агрегаты считаются силой SQL, данные переживают перезапуск
сервера (наблюдения, поставленные в прошлой сессии, продолжают копиться
в следующей).

Запуск (обычно его запускает agent_app.py как подпроцесс):
    python mcp_weather_journal_server.py

Переменные окружения:
    JOURNAL_INTERVAL_SEC  — интервал планировщика, сек (по умолчанию 600)
    JOURNAL_DB            — путь к файлу SQLite (по умолчанию weather_journal.db)
"""

import asyncio
import logging
import os
import sqlite3
import sys
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Annotated, Any

import httpx
from pydantic import BaseModel, Field

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

# httpx логирует каждый HTTP-запрос на stderr с уровнем INFO — приглушаем,
# чтобы не шуметь в консоли процесса-родителя (агентского приложения).
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
REQUEST_TIMEOUT = 15.0

DB_PATH = Path(os.getenv("JOURNAL_DB") or Path(__file__).with_name("weather_journal.db"))
COLLECT_INTERVAL = float(os.getenv("JOURNAL_INTERVAL_SEC", "600"))

# Человекочитаемые расшифровки кодов погоды WMO (как в day-2).
WMO_CONDITIONS: dict[int, str] = {
    0: "ясно",
    1: "преимущественно ясно",
    2: "переменная облачность",
    3: "пасмурно",
    45: "туман",
    48: "изморозь и туман",
    51: "слабая морось",
    53: "морось",
    55: "сильная морось",
    56: "ледяная морось",
    57: "сильная ледяная морось",
    61: "слабый дождь",
    63: "дождь",
    65: "сильный дождь",
    66: "ледяной дождь",
    67: "сильный ледяной дождь",
    71: "слабый снег",
    73: "снег",
    75: "сильный снег",
    77: "снежные зёрна",
    80: "слабый ливень",
    81: "ливень",
    82: "сильный ливень",
    85: "снежный ливень",
    86: "сильный снежный ливень",
    95: "гроза",
    96: "гроза с градом",
    99: "сильная гроза с градом",
}


def describe_code(code: int) -> str:
    """Код WMO → русское описание; для неизвестного кода — честная заглушка."""
    return WMO_CONDITIONS.get(code, "нет описания (неизвестный код WMO)")


def utc_now_iso() -> str:
    """Текущее время UTC в ISO-8601 — под ним храним замеры и сравниваем окна."""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def city_key(name: str) -> str:
    """Нормализация имени города для сопоставления (регистр/ё не важны)."""
    return name.strip().lower().replace("ё", "е")


# --------------------------------------------------------------------------- #
# Хранилище: SQLite. Схема — две таблицы: watches (что наблюдаем) и
# observations (накопленные замеры). Все запросы короткие, обращения идут
# из одного потока event loop, поэтому синхронный sqlite3 не блокирует
# заметно и не требует блокировок.
# --------------------------------------------------------------------------- #

SCHEMA = """
CREATE TABLE IF NOT EXISTS watches (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    city_key       TEXT UNIQUE NOT NULL,
    name           TEXT NOT NULL,
    country        TEXT,
    latitude       REAL NOT NULL,
    longitude      REAL NOT NULL,
    timezone       TEXT,
    created_at_utc TEXT NOT NULL,
    active         INTEGER NOT NULL DEFAULT 1  -- 0 = снят с наблюдения, история жива
);

CREATE TABLE IF NOT EXISTS observations (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    watch_id         INTEGER NOT NULL REFERENCES watches(id) ON DELETE CASCADE,
    observed_at_utc  TEXT NOT NULL,
    local_time       TEXT,
    temperature_c    REAL,
    feels_like_c     REAL,
    humidity_pct     REAL,
    precipitation_mm REAL,
    wind_kph         REAL,
    weather_code     INTEGER,
    condition        TEXT,
    source           TEXT NOT NULL DEFAULT 'scheduler'  -- 'scheduler' | 'manual'
);

CREATE INDEX IF NOT EXISTS idx_obs_watch_time ON observations(watch_id, observed_at_utc);
"""


def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    with db_connect() as conn:
        conn.executescript(SCHEMA)
        # мягкая миграция старых файлов БД (до появления колонки active)
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(watches)")}
        if "active" not in cols:
            conn.execute("ALTER TABLE watches ADD COLUMN active INTEGER NOT NULL DEFAULT 1")


def upsert_watch(city: dict[str, Any]) -> sqlite3.Row:
    with db_connect() as conn:
        conn.execute(
            """
            INSERT INTO watches (city_key, name, country, latitude, longitude, timezone, created_at_utc)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(city_key) DO UPDATE SET
                name = excluded.name, country = excluded.country,
                latitude = excluded.latitude, longitude = excluded.longitude,
                timezone = excluded.timezone, active = 1
            """,
            (
                city_key(city["name"]),
                city["name"],
                city.get("country"),
                city["latitude"],
                city["longitude"],
                city.get("timezone"),
                utc_now_iso(),
            ),
        )
        row = conn.execute(
            "SELECT * FROM watches WHERE city_key = ?", (city_key(city["name"]),)
        ).fetchone()
        return row


def deactivate_watch(name: str) -> sqlite3.Row | None:
    """«Снимает с наблюдения» мягко: строка и журнал замеров остаются."""
    with db_connect() as conn:
        row = conn.execute(
            "SELECT * FROM watches WHERE city_key = ? AND active = 1", (city_key(name),)
        ).fetchone()
        if row is not None:
            conn.execute("UPDATE watches SET active = 0 WHERE id = ?", (row["id"],))
        return row


def list_watch_rows() -> list[sqlite3.Row]:
    with db_connect() as conn:
        return conn.execute("SELECT * FROM watches WHERE active = 1 ORDER BY name").fetchall()


def find_watch(name: str, active_only: bool = True) -> sqlite3.Row | None:
    """Поиск города по нормализованному имени.

    active_only=False нужен get_weather_stats: история снятого с наблюдения
    города остаётся доступной, хотя планировщик его больше не опрашивает.
    """
    with db_connect() as conn:
        if active_only:
            return conn.execute(
                "SELECT * FROM watches WHERE city_key = ? AND active = 1", (city_key(name),)
            ).fetchone()
        return conn.execute(
            "SELECT * FROM watches WHERE city_key = ?", (city_key(name),)
        ).fetchone()


def insert_observation(watch_id: int, obs: dict[str, Any], source: str) -> None:
    with db_connect() as conn:
        conn.execute(
            """
            INSERT INTO observations (
                watch_id, observed_at_utc, local_time, temperature_c, feels_like_c,
                humidity_pct, precipitation_mm, wind_kph, weather_code, condition, source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                watch_id,
                utc_now_iso(),
                obs.get("local_time"),
                obs.get("temperature_c"),
                obs.get("feels_like_c"),
                obs.get("humidity_pct"),
                obs.get("precipitation_mm"),
                obs.get("wind_kph"),
                obs.get("weather_code"),
                obs.get("condition"),
                source,
            ),
        )


def window_start_iso(hours: float) -> str:
    from datetime import datetime, timedelta, timezone

    moment = datetime.now(timezone.utc) - timedelta(hours=hours)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- #
# Внешний API: Open-Meteo (геокодинг + текущая погода), как в day-2.
# --------------------------------------------------------------------------- #


async def geocode(http: httpx.AsyncClient, city: str) -> dict[str, Any]:
    resp = await http.get(
        GEOCODING_URL,
        params={"name": city, "count": 1, "language": "ru", "format": "json"},
    )
    resp.raise_for_status()
    results = resp.json().get("results") or []
    if not results:
        raise LookupError(f"город «{city}» не найден")
    top = results[0]
    return {
        "name": top.get("name", city),
        "country": top.get("country"),
        "latitude": top["latitude"],
        "longitude": top["longitude"],
        "timezone": top.get("timezone"),
    }


async def fetch_current(http: httpx.AsyncClient, latitude: float, longitude: float) -> dict[str, Any]:
    resp = await http.get(
        FORECAST_URL,
        params={
            "latitude": latitude,
            "longitude": longitude,
            "current": ",".join(
                [
                    "temperature_2m",
                    "apparent_temperature",
                    "relative_humidity_2m",
                    "precipitation",
                    "weather_code",
                    "wind_speed_10m",
                ]
            ),
            "timezone": "auto",
        },
    )
    resp.raise_for_status()
    payload = resp.json()
    if isinstance(payload.get("error"), dict):
        raise RuntimeError(f"Forecast API: {payload['error'].get('reason') or payload['error']}")
    current = payload["current"]
    code = int(current["weather_code"])
    return {
        "local_time": current.get("time"),
        "temperature_c": current["temperature_2m"],
        "feels_like_c": current["apparent_temperature"],
        "humidity_pct": current["relative_humidity_2m"],
        "precipitation_mm": current["precipitation"],
        "wind_kph": current["wind_speed_10m"],
        "weather_code": code,
        "condition": describe_code(code),
    }


async def collect_all(http: httpx.AsyncClient, source: str) -> list[dict[str, Any]]:
    """Один проход сборщика: опросить все города из watches, записать замеры."""
    report: list[dict[str, Any]] = []
    for watch in list_watch_rows():
        try:
            obs = await fetch_current(http, watch["latitude"], watch["longitude"])
            insert_observation(watch["id"], obs, source)
            report.append(
                {
                    "city": watch["name"],
                    "ok": True,
                    "temperature_c": obs["temperature_c"],
                    "condition": obs["condition"],
                    "error": None,
                }
            )
        except Exception as exc:  # сборщик не должен падать целиком из-за одного города
            report.append({"city": watch["name"], "ok": False, "temperature_c": None, "condition": None, "error": str(exc)})
    return report


def log(msg: str) -> None:
    """Лог сервера → stderr (stdout занят JSON-RPC). Виден в консоли родителя."""
    print(msg, file=sys.stderr, flush=True)


# --------------------------------------------------------------------------- #
# Модели результата — из них MCP строит outputSchema (как в day-2).
# --------------------------------------------------------------------------- #


class WatchRegistration(BaseModel):
    """Ответ watch_city: город зарегистрирован, замеры отложены."""

    name: str = Field(description="Имя города (как его вернул геокодер)")
    country: str | None = Field(description="Страна", default=None)
    latitude: float = Field(description="Широта точки наблюдения, °")
    longitude: float = Field(description="Долгота точки наблюдения, °")
    timezone: str | None = Field(description="Часовой пояс IANA", default=None)
    created_at_utc: str = Field(description="Время регистрации, UTC ISO-8601")
    scheduler_interval_sec: float = Field(description="Интервал планировщика, с (замеры идут в фоне)")
    first_sample_in_sec: float = Field(description="Ожидаемая задержка до первого замера, с")
    note: str = Field(description="Пояснение: замер выполнен НЕ сейчас, а ближайшим тиком планировщика")


class WatchSummary(BaseModel):
    """Один город из списка наблюдений."""

    name: str = Field(description="Имя города")
    country: str | None = Field(description="Страна", default=None)
    latitude: float = Field(description="Широта, °")
    longitude: float = Field(description="Долгота, °")
    timezone: str | None = Field(description="Часовой пояс IANA", default=None)
    created_at_utc: str = Field(description="Когда поставлен под наблюдение, UTC")
    samples_total: int = Field(description="Сколько замеров всего накоплено в журнале")
    last_observed_at_utc: str | None = Field(description="Время последнего замера, UTC", default=None)
    last_temperature_c: float | None = Field(description="Температура последнего замера, °C", default=None)
    last_condition: str | None = Field(description="Погода последнего замера", default=None)


class WatchList(BaseModel):
    """Сводка наблюдений: что копим и как часто."""

    scheduler_interval_sec: float = Field(description="Интервал планировщика, с")
    watches: list[WatchSummary] = Field(description="Наблюдаемые города")


class WatchRemoval(BaseModel):
    """Ответ unwatch_city."""

    name: str = Field(description="Имя города, снятого с наблюдения")
    samples_kept: int = Field(description="Сколько замеров этого города осталось в журнале (данные не удаляются)")


class CollectedSample(BaseModel):
    """Результат одного города в форсированном сборе."""

    city: str = Field(description="Имя города")
    ok: bool = Field(description="Замер прошёл успешно")
    temperature_c: float | None = Field(description="Температура, °C", default=None)
    condition: str | None = Field(description="Погода по-русски", default=None)
    error: str | None = Field(description="Текст ошибки, если замер не удался", default=None)


class CollectionReport(BaseModel):
    """Ответ collect_now: что собрано прямо сейчас."""

    collected_at_utc: str = Field(description="Время сбора, UTC ISO-8601")
    ok_count: int = Field(description="Сколько городов собрано успешно")
    fail_count: int = Field(description="Сколько городов не удалось опросить")
    samples: list[CollectedSample] = Field(description="Результат по каждому городу")


class LatestReading(BaseModel):
    """Последний замер журнала."""

    observed_at_utc: str = Field(description="Время замера, UTC ISO-8601")
    local_time: str | None = Field(description="Локальное время точки", default=None)
    temperature_c: float = Field(description="Температура, °C")
    feels_like_c: float = Field(description="Ощущаемая температура, °C")
    humidity_pct: float = Field(description="Влажность, %")
    precipitation_mm: float = Field(description="Осадки за последний час, мм")
    wind_kph: float = Field(description="Ветер, км/ч")
    condition: str = Field(description="Погода по-русски")


class CityStats(BaseModel):
    """Агрегированный результат get_weather_stats — то, ради чего копится журнал."""

    city: str = Field(description="Имя города")
    window_hours: float = Field(description="Окно агрегации, ч")
    sample_count: int = Field(description="Замеров в окне")
    first_observed_at_utc: str | None = Field(description="Начало окна фактических данных, UTC", default=None)
    last_observed_at_utc: str | None = Field(description="Конец окна фактических данных, UTC", default=None)
    avg_temperature_c: float | None = Field(description="Средняя температура в окне, °C (SQL AVG)", default=None)
    min_temperature_c: float | None = Field(description="Минимальная температура, °C (SQL MIN)", default=None)
    max_temperature_c: float | None = Field(description="Максимальная температура, °C (SQL MAX)", default=None)
    avg_feels_like_c: float | None = Field(description="Средняя ощущаемая температура, °C", default=None)
    avg_humidity_pct: float | None = Field(description="Средняя влажность, %", default=None)
    total_precipitation_mm: float | None = Field(description="Суммарные осадки за окно, мм (SQL SUM)", default=None)
    max_wind_kph: float | None = Field(description="Максимальный ветер, км/ч", default=None)
    dominant_condition: str | None = Field(description="Самая частая погода в окне (GROUP BY + COUNT)", default=None)
    temperature_trend: str = Field(description="Тренд: сравнение половин окна («потеплело/похолодало/стабильно»)")
    latest: LatestReading | None = Field(description="Последний замер, если есть", default=None)


# --------------------------------------------------------------------------- #
# Сервер, планировщик и инструменты.
# --------------------------------------------------------------------------- #

# Ресурсы, живущие столько же, сколько сервер: HTTP-клиент к Open-Meteo и
# фоновая задача-планировщик. Создаются в lifespan, убираются при остановке.
http_client: httpx.AsyncClient | None = None
scheduler_task: asyncio.Task[None] | None = None


async def scheduler_loop(interval: float) -> None:
    """ПЕРИОДИЧЕСКОЕ выполнение: каждые interval секунд — сбор по всем городам.

    Первый тик — через interval после старта (не сразу): города, добавленные
    watch_city, получают первый замер отложенно, ровно как заявлено в ответе
    инструмента.
    """
    tick = 0
    while True:
        await asyncio.sleep(interval)
        tick += 1
        if http_client is None:  # теоретически невозможно при жизни lifespan
            continue
        report = await collect_all(http_client, source="scheduler")
        ok = sum(1 for r in report if r["ok"])
        log(f"[планировщик] тик #{tick}: замеров записано {ok}/{len(report)}")
        for r in report:
            if not r["ok"]:
                log(f"[планировщик] {r['city']}: ошибка — {r['error']}")


@asynccontextmanager
async def lifespan(app: MCPServer):
    """Старт/стоп сервера: готовим БД, HTTP-клиент и фоновый планировщик."""
    global http_client, scheduler_task
    init_db()
    http_client = httpx.AsyncClient(timeout=REQUEST_TIMEOUT)
    scheduler_task = asyncio.create_task(scheduler_loop(COLLECT_INTERVAL))
    log(f"[журнал] сервер стартовал: БД={DB_PATH.name}, интервал планировщика {COLLECT_INTERVAL:g} с")
    try:
        yield {}
    finally:
        if scheduler_task is not None:
            scheduler_task.cancel()
            with suppress(asyncio.CancelledError):
                await scheduler_task
        await http_client.aclose()
        log("[журнал] сервер остановлен, данные сохранены в SQLite")


server = MCPServer(
    name="weather-journal",
    version="1.0.0",
    title="Weather Journal",
    instructions=(
        "MCP-сервер «метеожурнал»: ведёт журнал погоды наблюдаемых городов. "
        "Фоновый планировщик периодически опрашивает Open-Meteo и копит замеры в SQLite. "
        "Типовой сценарий: watch_city → подождать тики планировщика (или collect_now) → "
        "get_weather_stats для агрегата (средняя/мин/макс температура, осадки, тренд). "
        "Первый замер после watch_city отложен до ближайшего тика планировщика."
    ),
    lifespan=lifespan,
)


@server.tool()
async def watch_city(
    city: Annotated[str, Field(description="Название города, напр. «Санкт-Петербург» или «Sofia»")],
) -> WatchRegistration:
    """Ставит город под периодическое наблюдение — ЗАМЕР ОТЛОЖЕН.

    Вызов только регистрирует город (геокодинг + запись в SQLite) и сразу
    возвращается: сам он погоду НЕ измеряет. Замеры начнёт фоновый планировщик
    на ближайшем тике (см. first_sample_in_sec) и дальше будет повторять их
    каждые scheduler_interval_sec. Чтобы не ждать тика, можно форсировать
    замер инструментом collect_now. Статистика накопленного — get_weather_stats.
    """
    assert http_client is not None, "lifespan не запустился"
    try:
        geo = await geocode(http_client, city)
    except LookupError as exc:
        raise ToolError(f"{exc}. Проверьте написание или попробуйте английский вариант.") from exc
    except httpx.HTTPError as exc:
        raise ToolError(f"Geocoding API Open-Meteo недоступен: {exc}") from exc

    row = upsert_watch(geo)
    return WatchRegistration(
        name=row["name"],
        country=row["country"],
        latitude=row["latitude"],
        longitude=row["longitude"],
        timezone=row["timezone"],
        created_at_utc=row["created_at_utc"],
        scheduler_interval_sec=COLLECT_INTERVAL,
        first_sample_in_sec=COLLECT_INTERVAL,
        note=(
            "Город под наблюдением. Этот вызов погоду не измерял — первый замер "
            "сделает фоновый планировщик (отложенное выполнение). Для немедленного "
            "замера вызовите collect_now."
        ),
    )


@server.tool()
async def unwatch_city(
    city: Annotated[str, Field(description="Название города, как в watch_city")],
) -> WatchRemoval:
    """Снимает город с периодического наблюдения.

    Сам журнал не удаляется: накопленные замеры остаются в SQLite и доступны
    get_weather_stats — можно снять город с наблюдения и всё равно посмотреть
    его историю.
    """
    row = deactivate_watch(city)
    if row is None:
        raise ToolError(f"Город «{city}» не наблюдается. Список наблюдений — list_watches.")
    with db_connect() as conn:
        kept = conn.execute(
            "SELECT COUNT(*) FROM observations WHERE watch_id = ?", (row["id"],)
        ).fetchone()[0]
    return WatchRemoval(name=row["name"], samples_kept=kept)


@server.tool()
async def list_watches() -> WatchList:
    """Показывает все города под наблюдением и состояние их журналов.

    Для каждого города: сколько замеров накоплено, когда был последний и каким
    он был. Полезно, чтобы понять, что уже успел собрать планировщик.
    """
    summaries: list[WatchSummary] = []
    with db_connect() as conn:
        for watch in conn.execute(
            "SELECT * FROM watches WHERE active = 1 ORDER BY name"
        ).fetchall():
            agg = conn.execute(
                """
                SELECT COUNT(*) AS total, MAX(observed_at_utc) AS last_at
                FROM observations WHERE watch_id = ?
                """,
                (watch["id"],),
            ).fetchone()
            last = conn.execute(
                """
                SELECT temperature_c, condition FROM observations
                WHERE watch_id = ?
                ORDER BY observed_at_utc DESC, id DESC LIMIT 1
                """,
                (watch["id"],),
            ).fetchone()
            summaries.append(
                WatchSummary(
                    name=watch["name"],
                    country=watch["country"],
                    latitude=watch["latitude"],
                    longitude=watch["longitude"],
                    timezone=watch["timezone"],
                    created_at_utc=watch["created_at_utc"],
                    samples_total=agg["total"],
                    last_observed_at_utc=agg["last_at"],
                    last_temperature_c=last["temperature_c"] if last else None,
                    last_condition=last["condition"] if last else None,
                )
            )
    return WatchList(scheduler_interval_sec=COLLECT_INTERVAL, watches=summaries)


@server.tool()
async def collect_now() -> CollectionReport:
    """Форсирует замер по всем наблюдаемым городам немедленно (без тика).

    Обходит планировщик: опрашивает Open-Meteo по каждому городу из watches и
    пишет замеры в журнал с пометкой source=manual. Удобно, когда данные нужны
    «сейчас», а не через интервал планировщика.
    """
    assert http_client is not None, "lifespan не запустился"
    report = await collect_all(http_client, source="manual")
    ok_count = sum(1 for r in report if r["ok"])
    log(f"[collect_now] ручной сбор: записано {ok_count}/{len(report)}")
    return CollectionReport(
        collected_at_utc=utc_now_iso(),
        ok_count=ok_count,
        fail_count=len(report) - ok_count,
        samples=[CollectedSample(**r) for r in report],
    )


@server.tool()
async def get_weather_stats(
    city: Annotated[str, Field(description="Название города из watch_city")],
    hours: Annotated[float, Field(description="Окно агрегации, часов (1–720)", ge=1.0, le=720.0)] = 1.0,
) -> CityStats:
    """Возвращает агрегированную статистику журнала по городу за окно времени.

    Агрегаты считает SQLite (AVG/MIN/MAX/SUM/COUNT) плюс доминирующая погода
    (GROUP BY) и тренд температуры (сравнение первой и второй половины окна).
    Если в окне ещё нет замеров — подскажет, сколько ждать или как форсировать
    сбор (collect_now).
    """
    watch = find_watch(city, active_only=False)
    if watch is None:
        raise ToolError(
            f"Город «{city}» не наблюдается. Сначала watch_city, затем ждите тики "
            "планировщика или вызовите collect_now."
        )

    since = window_start_iso(hours)
    with db_connect() as conn:
        base = conn.execute(
            """
            SELECT COUNT(*) AS n,
                   MIN(observed_at_utc) AS first_at, MAX(observed_at_utc) AS last_at,
                   AVG(temperature_c) AS avg_t, MIN(temperature_c) AS min_t, MAX(temperature_c) AS max_t,
                   AVG(feels_like_c) AS avg_feels, AVG(humidity_pct) AS avg_hum,
                   SUM(precipitation_mm) AS total_precip, MAX(wind_kph) AS max_wind
            FROM observations
            WHERE watch_id = ? AND observed_at_utc >= ?
            """,
            (watch["id"], since),
        ).fetchone()

        if base["n"] == 0:
            raise ToolError(
                f"По «{watch['name']}» нет замеров за последние {hours:g} ч. "
                f"Первый замер — в течение {COLLECT_INTERVAL:g} с после watch_city "
                "(отложенно), либо форсируйте collect_now."
            )

        dominant = conn.execute(
            """
            SELECT condition, COUNT(*) AS n
            FROM observations
            WHERE watch_id = ? AND observed_at_utc >= ? AND condition IS NOT NULL
            GROUP BY condition ORDER BY n DESC LIMIT 1
            """,
            (watch["id"], since),
        ).fetchone()

        # Тренд: средняя первой половины окна против второй (SQL ROW_NUMBER).
        trend = "стабильно (в окне менее двух замеров)"
        if base["n"] >= 2:
            halves = conn.execute(
                """
                SELECT
                  AVG(CASE WHEN rn <= total / 2.0 THEN temperature_c END) AS first_half,
                  AVG(CASE WHEN rn >  total / 2.0 THEN temperature_c END) AS second_half
                FROM (
                  SELECT temperature_c,
                         ROW_NUMBER() OVER (ORDER BY observed_at_utc, id) AS rn,
                         COUNT(*) OVER () AS total
                  FROM observations
                  WHERE watch_id = ? AND observed_at_utc >= ?
                )
                """,
                (watch["id"], since),
            ).fetchone()
            if halves["first_half"] is not None and halves["second_half"] is not None:
                delta = halves["second_half"] - halves["first_half"]
                if delta > 0.5:
                    trend = f"потеплело на {delta:+.1f} °C за окно"
                elif delta < -0.5:
                    trend = f"похолодало на {delta:+.1f} °C за окно"
                else:
                    trend = f"стабильно ({delta:+.1f} °C между половинами окна)"

        latest_row = conn.execute(
            """
            SELECT * FROM observations WHERE watch_id = ?
            ORDER BY observed_at_utc DESC, id DESC LIMIT 1
            """,
            (watch["id"],),
        ).fetchone()

    latest = (
        LatestReading(
            observed_at_utc=latest_row["observed_at_utc"],
            local_time=latest_row["local_time"],
            temperature_c=latest_row["temperature_c"],
            feels_like_c=latest_row["feels_like_c"],
            humidity_pct=latest_row["humidity_pct"],
            precipitation_mm=latest_row["precipitation_mm"],
            wind_kph=latest_row["wind_kph"],
            condition=latest_row["condition"],
        )
        if latest_row is not None
        else None
    )

    return CityStats(
        city=watch["name"],
        window_hours=hours,
        sample_count=base["n"],
        first_observed_at_utc=base["first_at"],
        last_observed_at_utc=base["last_at"],
        avg_temperature_c=base["avg_t"],
        min_temperature_c=base["min_t"],
        max_temperature_c=base["max_t"],
        avg_feels_like_c=base["avg_feels"],
        avg_humidity_pct=base["avg_hum"],
        total_precipitation_mm=base["total_precip"],
        max_wind_kph=base["max_wind"],
        dominant_condition=dominant["condition"] if dominant else None,
        temperature_trend=trend,
        latest=latest,
    )


if __name__ == "__main__":
    # Логи SDK и планировщика идут в stderr — на Windows он по умолчанию не
    # UTF-8, из-за чего кириллица превращается в «кракозябры». stdout не
    # трогаем: он занят JSON-RPC.
    if sys.platform == "win32" and sys.stderr.encoding and sys.stderr.encoding.lower() not in ("utf-8", "utf8"):
        sys.stderr.reconfigure(encoding="utf-8")
    server.run("stdio")
