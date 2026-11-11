"""
Week 4 · Day 4 — MCP Composition: Auto Pipeline (композиция и автоцепочка).

Задание: создать несколько MCP-инструментов и автоматическую цепочку
«получение данных → обработка → сохранение»; проверить выполнение цепочки
и передачу данных между стадиями.

Этот файл — MCP-СЕРВЕР (транспорт stdio) «weather-pipeline». Развитие дней
2–3: там агент вызывал инструменты по одному, здесь появляется КОМПОЗИЦИЯ —
конвейер из трёх стадий, где данные передаются между стадиями по artifact_id,
а факт передачи проверяется хэшем sha256:

  Стадия 1  fetch_weather  (получение данных)  — Open-Meteo: геокодинг +
            текущая погода + прогноз на 7 дней → артефакт raw_weather;
  Стадия 2  analyze_data   (обработка)         — детерминированные агрегаты
            (средняя/мин/макс температура, осадки, дождливые дни, доминирующая
            погода) + текстовый вывод LLM GLM (Z.ai) с fallback на правила →
            артефакт weather_analysis, хранящий ссылку и ХЭШ исходных данных;
  Стадия 3  save_report    (сохранение)        — отчёт в SQLite + JSON-файл
            reports/report_NNN.json с блоком lineage («родословная» данных).

Два режима композиции:
  • автоматический — run_pipeline(city): сервер сам прогоняет все стадии и
    логирует каждую в таблицу stages (статус, вход/выход, длительность);
  • ручной — агент (LLM) сам вызывает инструменты по очереди и передаёт
    artifact_id от инструмента к инструменту.

Проверка цепочки и передачи данных — inspect_run(run_id): автоматический
аудит запуска — порядок и статусы стадий, связность ссылок artifact:N,
совпадение хэшей «что отдал fetch = что получил analyze», наличие файла
отчёта на диске.

Хранение — SQLite (pipeline.db, стандартная библиотека): artifacts (данные
стадий с хэшем), runs/stages (журнал конвейера), reports (сохранённые
отчёты). Данные переживают перезапуск сервера.

Переменные окружения:
    PIPELINE_DB  — путь к файлу SQLite (по умолчанию pipeline.db рядом со скриптом)
    ZAI_API_KEY  — ключ Z.ai для LLM-анализа на стадии обработки
                   (нет ключа или сбой → детерминированный разбор по правилам)
    ZAI_ENDPOINT, ZAI_MODEL — эндпоинт/модель LLM (по умолчанию coding GLM)

Запуск (обычно его запускает agent_app.py как подпроцесс):
    python mcp_pipeline_server.py
"""

import hashlib
import json
import logging
import os
import sqlite3
import sys
import time
from collections import Counter
from contextlib import asynccontextmanager
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

# LLM для стадии «обработка»: тот же coding-эндпоинт Z.ai, что у агента
# в днях 2–3 (ключ не обязателен — при его отсутствии сработает fallback).
DEFAULT_ZAI_ENDPOINT = "https://api.z.ai/api/coding/paas/v4/chat/completions"
DEFAULT_MODEL = "glm-4.6"

DB_PATH = Path(os.getenv("PIPELINE_DB") or Path(__file__).with_name("pipeline.db"))
REPORTS_DIR = Path(__file__).with_name("reports")

# Человекочитаемые расшифровки кодов погоды WMO (как в day-2/day-3).
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
    67: "сильной ледяной дождь",
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
    """Текущее время UTC в ISO-8601."""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def canonical_json(payload: Any) -> str:
    """Канонический JSON: одинаковое представление → одинаковый sha256."""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def log(msg: str) -> None:
    """Лог сервера → stderr (stdout занят JSON-RPC). Виден в консоли родителя."""
    print(msg, file=sys.stderr, flush=True)


# --------------------------------------------------------------------------- #
# Хранилище: SQLite. Четыре таблицы конвейера:
#   artifacts — данные каждой стадии (kind, JSON payload, sha256-хэш);
#   runs      — запуски автоцепочки (run_pipeline);
#   stages    — пострадийный журнал запуска (вход/выход, статус, длительность);
#   reports   — сохранённые отчёты (результат стадии «сохранение»).
# --------------------------------------------------------------------------- #

SCHEMA = """
CREATE TABLE IF NOT EXISTS artifacts (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    kind           TEXT NOT NULL,               -- 'raw_weather' | 'weather_analysis'
    created_at_utc TEXT NOT NULL,
    payload        TEXT NOT NULL,               -- канонический JSON данных стадии
    payload_hash   TEXT NOT NULL                -- sha256(payload) — «отпечаток» данных
);

CREATE TABLE IF NOT EXISTS runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    city            TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'running',   -- 'running' | 'ok' | 'failed'
    error           TEXT,
    started_at_utc  TEXT NOT NULL,
    finished_at_utc TEXT
);

CREATE TABLE IF NOT EXISTS stages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    position    INTEGER NOT NULL,               -- 1 | 2 | 3
    name        TEXT NOT NULL,                  -- 'fetch' | 'analyze' | 'save'
    status      TEXT NOT NULL,                  -- 'ok' | 'failed'
    input_ref   TEXT,                           -- 'artifact:N' — что потребили
    output_ref  TEXT,                           -- 'artifact:N' | 'report:N' — что отдали
    detail      TEXT,
    duration_ms INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS reports (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    title          TEXT NOT NULL,
    artifact_id    INTEGER NOT NULL REFERENCES artifacts(id),
    city           TEXT NOT NULL,
    file_path      TEXT NOT NULL,
    created_at_utc TEXT NOT NULL
);
"""


def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    with db_connect() as conn:
        conn.executescript(SCHEMA)


def store_artifact(kind: str, payload: dict[str, Any]) -> sqlite3.Row:
    """Сохраняет данные стадии как артефакт; хэш считается по каноническому JSON."""
    text = canonical_json(payload)
    digest = hashlib.sha256(text.encode()).hexdigest()
    with db_connect() as conn:
        cur = conn.execute(
            "INSERT INTO artifacts (kind, created_at_utc, payload, payload_hash) VALUES (?, ?, ?, ?)",
            (kind, utc_now_iso(), text, digest),
        )
        return conn.execute("SELECT * FROM artifacts WHERE id = ?", (cur.lastrowid,)).fetchone()


def load_artifact(artifact_id: int) -> sqlite3.Row | None:
    with db_connect() as conn:
        return conn.execute("SELECT * FROM artifacts WHERE id = ?", (artifact_id,)).fetchone()


def artifact_payload(row: sqlite3.Row) -> dict[str, Any]:
    return json.loads(row["payload"])


def parse_ref(ref: str | None) -> int | None:
    """'artifact:3' / 'report:1' → 3 / 1; мусор → None."""
    if not ref or ":" not in ref:
        return None
    try:
        return int(ref.split(":", 1)[1])
    except ValueError:
        return None


def create_run(city: str) -> int:
    with db_connect() as conn:
        cur = conn.execute(
            "INSERT INTO runs (city, status, started_at_utc) VALUES (?, 'running', ?)",
            (city, utc_now_iso()),
        )
        return int(cur.lastrowid)


def record_stage(
    run_id: int,
    position: int,
    name: str,
    status: str,
    input_ref: str | None,
    output_ref: str | None,
    detail: str | None,
    duration_ms: int,
) -> None:
    with db_connect() as conn:
        conn.execute(
            """
            INSERT INTO stages (run_id, position, name, status, input_ref, output_ref, detail, duration_ms)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (run_id, position, name, status, input_ref, output_ref, detail, duration_ms),
        )


def finish_run(run_id: int, status: str, error: str | None = None) -> None:
    with db_connect() as conn:
        conn.execute(
            "UPDATE runs SET status = ?, error = ?, finished_at_utc = ? WHERE id = ?",
            (status, error, utc_now_iso(), run_id),
        )


# --------------------------------------------------------------------------- #
# Внешние API: Open-Meteo (геокодинг + прогноз), как в day-2/day-3.
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


async def fetch_forecast(http: httpx.AsyncClient, latitude: float, longitude: float) -> dict[str, Any]:
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
            "daily": ",".join(
                [
                    "temperature_2m_max",
                    "temperature_2m_min",
                    "precipitation_sum",
                    "precipitation_probability_max",
                    "wind_speed_10m_max",
                    "weather_code",
                ]
            ),
            "forecast_days": 7,
            "timezone": "auto",
        },
    )
    resp.raise_for_status()
    payload = resp.json()
    if isinstance(payload.get("error"), dict):
        raise RuntimeError(f"Forecast API: {payload['error'].get('reason') or payload['error']}")

    cur = payload["current"]
    code = int(cur["weather_code"])
    current = {
        "local_time": cur.get("time"),
        "temperature_c": cur["temperature_2m"],
        "feels_like_c": cur["apparent_temperature"],
        "humidity_pct": cur["relative_humidity_2m"],
        "precipitation_mm": cur["precipitation"],
        "wind_kph": cur["wind_speed_10m"],
        "weather_code": code,
        "condition": describe_code(code),
    }

    daily: list[dict[str, Any]] = []
    d = payload["daily"]
    for i, date in enumerate(d["time"]):
        dcode = int(d["weather_code"][i])
        daily.append(
            {
                "date": date,
                "t_min_c": d["temperature_2m_min"][i],
                "t_max_c": d["temperature_2m_max"][i],
                "precipitation_mm": d["precipitation_sum"][i],
                "precipitation_probability_pct": d["precipitation_probability_max"][i],
                "max_wind_kph": d["wind_speed_10m_max"][i],
                "weather_code": dcode,
                "condition": describe_code(dcode),
            }
        )
    return {"current": current, "daily": daily}


# --------------------------------------------------------------------------- #
# Стадия 2 «обработка»: агрегаты + текстовый вывод.
# LLM вызывается по желанию (use_llm) и при наличии ключа; любой сбой
# прозрачно заменяется детерминированным разбором по правилам.
# --------------------------------------------------------------------------- #


def comfort_label(avg_c: float) -> str:
    if avg_c < -10:
        return "морозно"
    if avg_c < 0:
        return "холодно"
    if avg_c < 8:
        return "прохладно"
    if avg_c < 16:
        return "умеренно"
    if avg_c < 24:
        return "тепло"
    return "жарко"


def compute_aggregates(daily: list[dict[str, Any]]) -> dict[str, Any]:
    means = [(d["t_min_c"] + d["t_max_c"]) / 2 for d in daily]
    conditions = [d["condition"] for d in daily]
    return {
        "days": len(daily),
        "avg_temperature_c": round(sum(means) / len(means), 1),
        "min_temperature_c": min(d["t_min_c"] for d in daily),
        "max_temperature_c": max(d["t_max_c"] for d in daily),
        "total_precipitation_mm": round(sum(d["precipitation_mm"] or 0 for d in daily), 1),
        "rainy_days": sum(
            1
            for d in daily
            if (d["precipitation_mm"] or 0) > 0.1 or (d["precipitation_probability_pct"] or 0) >= 50
        ),
        "max_wind_kph": max(d["max_wind_kph"] or 0 for d in daily),
        "dominant_condition": Counter(conditions).most_common(1)[0][0],
        "comfort": comfort_label(sum(means) / len(means)),
    }


def rule_summary(city: str, c: dict[str, Any]) -> str:
    return (
        f"За ближайшие {c['days']} дн. в {city}: средняя {c['avg_temperature_c']} °C "
        f"(от {c['min_temperature_c']} до {c['max_temperature_c']} °C), суммарно осадков "
        f"{c['total_precipitation_mm']} мм, дождливых дней {c['rainy_days']}, максимум ветра "
        f"{c['max_wind_kph']} км/ч. Преобладает: {c['dominant_condition']}; в целом — «{c['comfort']}». "
        "Совет: одевайтесь по многослойному принципу и держите под рукой зонт на дождливые дни."
    )


async def llm_summary(http: httpx.AsyncClient, city: str, c: dict[str, Any]) -> str | None:
    """Текстовый вывод LLM GLM по агрегатам; None → вызывающий берёт fallback."""
    api_key = os.getenv("ZAI_API_KEY", "")
    if not api_key or api_key == "your_api_key_here":
        return None
    endpoint = os.getenv("ZAI_ENDPOINT", DEFAULT_ZAI_ENDPOINT)
    model = os.getenv("ZAI_MODEL", DEFAULT_MODEL)
    prompt = (
        f"Ты — метеоаналитик. Агрегаты прогноза погоды на {c['days']} дней для города {city}:\n"
        f"{json.dumps(c, ensure_ascii=False)}\n"
        "Напиши 2–3 предложения на русском: краткая характеристика погоды и практический "
        "совет (одежда, зонт). Только текст, без markdown-разметки."
    )
    resp = await http.post(
        endpoint,
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "thinking": {"type": "disabled"},  # короткий фактический вывод
            "max_tokens": 400,
        },
    )
    data = resp.json()
    if not resp.is_success:
        raise RuntimeError(f"Z.ai HTTP {resp.status_code}: {data.get('error') or data}")
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError(f"Z.ai вернул пустой choices: {data}")
    text = (choices[0].get("message") or {}).get("content") or ""
    return text.strip() or None


# --------------------------------------------------------------------------- #
# Ядра стадий (do_*): их вызывают и отдельные инструменты, и run_pipeline —
# поэтому и ручная, и автоматическая цепочка делают одно и то же.
# --------------------------------------------------------------------------- #


async def do_fetch(http: httpx.AsyncClient, city: str) -> sqlite3.Row:
    geo = await geocode(http, city)
    forecast = await fetch_forecast(http, geo["latitude"], geo["longitude"])
    payload = {
        "city": geo,
        "fetched_at_utc": utc_now_iso(),
        "current": forecast["current"],
        "daily": forecast["daily"],
    }
    return store_artifact("raw_weather", payload)


def load_stage_input(artifact_id: int, expected_kind: str, next_hint: str) -> sqlite3.Row:
    row = load_artifact(artifact_id)
    if row is None:
        raise ToolError(f"Артефакт {artifact_id} не найден. {next_hint}")
    if row["kind"] != expected_kind:
        raise ToolError(
            f"Артефакт {artifact_id} имеет вид «{row['kind']}», а стадия ждёт «{expected_kind}». "
            f"Дисциплина цепочки: {next_hint}"
        )
    return row


async def do_analyze(http: httpx.AsyncClient, artifact_id: int, use_llm: bool) -> sqlite3.Row:
    raw = load_stage_input(
        artifact_id, "raw_weather", "сначала получите данные инструментом fetch_weather."
    )
    raw_payload = artifact_payload(raw)
    daily = raw_payload.get("daily") or []
    if not daily:
        raise ToolError("В артефакте нет дневного прогноза — данные неполные, повторите fetch_weather.")

    city = raw_payload["city"]["name"]
    computed = compute_aggregates(daily)

    summary, source = None, "rules"
    if use_llm:
        try:
            summary = await llm_summary(http, city, computed)
            if summary:
                source = "llm"
        except Exception as exc:  # LLM недоступен → работаем дальше по правилам
            log(f"[pipeline] LLM-анализ недоступен ({exc}) — переключаюсь на правила")
    if not summary:
        summary = rule_summary(city, computed)

    analysis = {
        # Доказательство передачи данных: ссылка на исходный артефакт и его хэш
        # В МОМЕНТ обработки. inspect_run пересчитает хэш и сравнит.
        "source_artifact_id": raw["id"],
        "input_payload_hash": raw["payload_hash"],
        "city": city,
        "computed": computed,
        "summary_text": summary,
        "summary_source": source,
        "analyzed_at_utc": utc_now_iso(),
    }
    return store_artifact("weather_analysis", analysis)


def do_save(artifact_id: int, title: str | None) -> dict[str, Any]:
    row = load_stage_input(
        artifact_id, "weather_analysis", "сначала обработайте данные инструментом analyze_data."
    )
    analysis = artifact_payload(row)
    city = analysis["city"]

    raw_id = analysis.get("source_artifact_id")
    raw = load_artifact(raw_id) if raw_id is not None else None
    raw_hash_now = raw["payload_hash"] if raw is not None else None
    raw_payload = artifact_payload(raw) if raw is not None else None
    matches = raw_hash_now is not None and raw_hash_now == analysis.get("input_payload_hash")

    lineage = {
        "raw_artifact_id": raw_id,
        "analysis_artifact_id": row["id"],
        "raw_payload_hash": raw_hash_now,
        "input_payload_hash": analysis.get("input_payload_hash"),
        "matches": matches,
    }
    report_doc = {
        "title": title or f"Погода: {city}",
        "generated_at_utc": utc_now_iso(),
        "city": city,
        "lineage": lineage,
        "analysis": analysis,
        "source_data": (
            {"current": raw_payload["current"], "daily": raw_payload["daily"]}
            if raw_payload is not None
            else None
        ),
    }

    REPORTS_DIR.mkdir(exist_ok=True)
    with db_connect() as conn:
        cur = conn.execute(
            "INSERT INTO reports (title, artifact_id, city, file_path, created_at_utc) "
            "VALUES (?, ?, ?, ?, ?)",
            (report_doc["title"], row["id"], city, "", report_doc["generated_at_utc"]),
        )
        report_id = int(cur.lastrowid)
    file_path = REPORTS_DIR / f"report_{report_id:03d}.json"
    file_path.write_text(json.dumps(report_doc, ensure_ascii=False, indent=2), encoding="utf-8")
    with db_connect() as conn:
        conn.execute("UPDATE reports SET file_path = ? WHERE id = ?", (str(file_path), report_id))

    return {
        "report_id": report_id,
        "title": report_doc["title"],
        "file_path": str(file_path),
        "lineage": lineage,
        "created_at_utc": report_doc["generated_at_utc"],
    }


# --------------------------------------------------------------------------- #
# Модели результата — из них MCP строит outputSchema (как в day-2/day-3).
# --------------------------------------------------------------------------- #


class FetchResult(BaseModel):
    """Ответ fetch_weather — артефакт стадии «получение данных»."""

    artifact_id: int = Field(description="id артефакта raw_weather — передайте в analyze_data")
    city: str = Field(description="Имя города (как вернул геокодер)")
    country: str | None = Field(description="Страна", default=None)
    days_count: int = Field(description="Сколько дней прогноза получено")
    current_temperature_c: float | None = Field(description="Температура сейчас, °C", default=None)
    current_condition: str | None = Field(description="Погода сейчас по-русски", default=None)
    payload_hash: str = Field(description="sha256 данных стадии — «отпечаток» для проверки передачи")
    next_step: str = Field(description="Что делать дальше в цепочке")


class AnalysisResult(BaseModel):
    """Ответ analyze_data — артефакт стадии «обработка»."""

    artifact_id: int = Field(description="id артефакта weather_analysis — передайте в save_report")
    source_artifact_id: int = Field(description="id исходного артефакта fetch_weather (откуда пришли данные)")
    city: str = Field(description="Город, к которому относится анализ")
    days: int = Field(description="Дней в анализе")
    avg_temperature_c: float | None = Field(description="Средняя температура, °C", default=None)
    min_temperature_c: float | None = Field(description="Минимальная температура, °C", default=None)
    max_temperature_c: float | None = Field(description="Максимальная температура, °C", default=None)
    total_precipitation_mm: float | None = Field(description="Суммарные осадки, мм", default=None)
    rainy_days: int = Field(description="Дождливых дней", default=0)
    max_wind_kph: float | None = Field(description="Максимальный ветер, км/ч", default=None)
    dominant_condition: str | None = Field(description="Доминирующая погода", default=None)
    comfort: str = Field(description="Классификация: морозно/холодно/прохладно/умеренно/тепло/жарко")
    summary_text: str = Field(description="Текстовый вывод: LLM (glm) или правила-fallback")
    summary_source: str = Field(description="'llm' — вывод сгенерировала модель; 'rules' — fallback по правилам")
    next_step: str = Field(description="Что делать дальше в цепочке")


class Lineage(BaseModel):
    """Родословная данных сохранённого отчёта — доказательство цепочки."""

    raw_artifact_id: int | None = Field(description="Артефакт стадии «получение данных»", default=None)
    analysis_artifact_id: int = Field(description="Артефакт стадии «обработка»")
    raw_payload_hash: str | None = Field(description="sha256 исходных данных СЕЙЧАС (пересчитан)", default=None)
    input_payload_hash: str | None = Field(description="sha256, зафиксированный при обработке", default=None)
    matches: bool = Field(description="true — данные дошли до обработки без изменений")


class ReportSaved(BaseModel):
    """Ответ save_report — стадия «сохранение» выполнена."""

    report_id: int = Field(description="id отчёта в SQLite")
    title: str = Field(description="Заголовок отчёта")
    file_path: str = Field(description="JSON-файл отчёта на диске")
    lineage: Lineage = Field(description="Цепочка artifact'ов и проверка совпадения хэшей")
    created_at_utc: str = Field(description="Время сохранения, UTC ISO-8601")
    note: str = Field(description="Пояснение к результату")


class StageInfo(BaseModel):
    """Одна стадия запуска конвейера из журнала stages."""

    position: int = Field(description="Номер стадии (1–3)")
    name: str = Field(description="Имя стадии: fetch | analyze | save")
    status: str = Field(description="ok | failed")
    input_ref: str | None = Field(description="Что получила на вход: 'artifact:N' (у fetch — null)", default=None)
    output_ref: str | None = Field(description="Что отдала: 'artifact:N' или 'report:N'", default=None)
    duration_ms: int = Field(description="Длительность стадии, мс")
    detail: str | None = Field(description="Подробность: город, источник вывода, имя файла", default=None)


class PipelineRunResult(BaseModel):
    """Ответ run_pipeline — автоматическая цепочка выполнена (или упала)."""

    run_id: int = Field(description="id запуска — передайте в inspect_run для аудита")
    city: str = Field(description="Город конвейера")
    status: str = Field(description="ok | failed")
    stages: list[StageInfo] = Field(description="Постадийный журнал: вход/выход, статус, длительность")
    report_id: int | None = Field(description="id сохранённого отчёта (при успехе)", default=None)
    file_path: str | None = Field(description="Файл отчёта (при успехе)", default=None)
    summary_text: str | None = Field(description="Текст стадии обработки (при успехе)", default=None)
    note: str = Field(description="Что делать дальше")


class CheckResult(BaseModel):
    """Одна проверка аудита цепочки."""

    name: str = Field(description="Имя проверки")
    ok: bool = Field(description="Пройдена ли")
    detail: str = Field(description="Что проверено и что обнаружено")


class RunInspection(BaseModel):
    """Ответ inspect_run — аудит выполнения цепочки и передачи данных."""

    run_id: int = Field(description="id аудируемого запуска")
    city: str = Field(description="Город запуска")
    status: str = Field(description="Статус запуска: ok | failed")
    all_ok: bool = Field(description="true — все проверки цепочки пройдены")
    stages: list[StageInfo] = Field(description="Журнал стадий запуска")
    checks: list[CheckResult] = Field(description="Результаты проверок (порядок, ссылки, хэши, файл)")
    conclusion: str = Field(description="Итоговый вердикт аудита")


class ReportInfo(BaseModel):
    """Короткая карточка сохранённого отчёта."""

    report_id: int
    title: str
    city: str
    file_path: str
    created_at_utc: str


class ReportList(BaseModel):
    """Ответ list_reports."""

    reports: list[ReportInfo] = Field(description="Последние сохранённые отчёты (новые сверху)")


# --------------------------------------------------------------------------- #
# Сервер и инструменты.
# --------------------------------------------------------------------------- #

# HTTP-клиент живёт столько же, сколько сервер: и для Open-Meteo (стадия 1),
# и для LLM Z.ai (стадия 2). Создаётся в lifespan, закрывается при остановке.
http_client: httpx.AsyncClient | None = None


@asynccontextmanager
async def lifespan(app: MCPServer):
    global http_client
    init_db()
    http_client = httpx.AsyncClient(timeout=60.0)
    log(f"[pipeline] сервер стартовал: БД={DB_PATH.name}, отчёты={REPORTS_DIR.name}/")
    try:
        yield {}
    finally:
        await http_client.aclose()
        log("[pipeline] сервер остановлен")


server = MCPServer(
    name="weather-pipeline",
    version="1.0.0",
    title="Weather Data Pipeline",
    instructions=(
        "MCP-сервер «конвейер данных»: получение → обработка → сохранение. "
        "Автоматическая цепочка — run_pipeline(city); проверка выполнения и передачи данных — "
        "inspect_run(run_id). Ручная цепочка (стадия за стадией): fetch_weather → "
        "analyze_data(artifact_id от fetch) → save_report(artifact_id от analyze), "
        "обязательно передавая artifact_id между вызовами. "
        "list_reports — сохранённые отчёты."
    ),
    lifespan=lifespan,
)


@server.tool()
async def fetch_weather(
    city: Annotated[str, Field(description="Название города, напр. «Санкт-Петербург» или «Sofia»")],
) -> FetchResult:
    """Стадия 1 «получение данных»: погода Open-Meteo (сейчас + 7 дней) → артефакт.

    Возвращает artifact_id — по нему следующая стадия (analyze_data) возьмёт
    данные; payload_hash — «отпечаток» данных для последующей проверки передачи.
    """
    assert http_client is not None, "lifespan не запустился"
    try:
        row = await do_fetch(http_client, city)
    except LookupError as exc:
        raise ToolError(f"{exc}. Проверьте написание или попробуйте английский вариант.") from exc
    except httpx.HTTPError as exc:
        raise ToolError(f"Open-Meteo недоступен: {exc}") from exc

    payload = artifact_payload(row)
    cur = payload["current"]
    return FetchResult(
        artifact_id=row["id"],
        city=payload["city"]["name"],
        country=payload["city"].get("country"),
        days_count=len(payload["daily"]),
        current_temperature_c=cur["temperature_c"],
        current_condition=cur["condition"],
        payload_hash=row["payload_hash"],
        next_step="Передайте artifact_id в analyze_data — стадия «обработка» конвейера.",
    )


@server.tool()
async def analyze_data(
    artifact_id: Annotated[int, Field(description="id артефакта от fetch_weather", ge=1)],
    use_llm: Annotated[bool, Field(description="Генерировать текстовый вывод через LLM GLM (иначе — по правилам)")] = True,
) -> AnalysisResult:
    """Стадия 2 «обработка»: агрегаты + текстовый вывод по данным артефакта.

    Принимает artifact_id от fetch_weather. Считает детерминированные агрегаты
    (средняя/мин/макс температура, осадки, дождливые дни, доминирующая погода)
    и генерирует короткий вывод: LLM GLM (если есть ключ) или по правилам.
    В артефакте фиксируются source_artifact_id и input_payload_hash — «доказательство»
    того, какие именно данные обработаны.
    """
    assert http_client is not None, "lifespan не запустился"
    row = await do_analyze(http_client, artifact_id, use_llm)
    p = artifact_payload(row)
    c = p["computed"]
    return AnalysisResult(
        artifact_id=row["id"],
        source_artifact_id=p["source_artifact_id"],
        city=p["city"],
        days=c["days"],
        avg_temperature_c=c["avg_temperature_c"],
        min_temperature_c=c["min_temperature_c"],
        max_temperature_c=c["max_temperature_c"],
        total_precipitation_mm=c["total_precipitation_mm"],
        rainy_days=c["rainy_days"],
        max_wind_kph=c["max_wind_kph"],
        dominant_condition=c["dominant_condition"],
        comfort=c["comfort"],
        summary_text=p["summary_text"],
        summary_source=p["summary_source"],
        next_step="Передайте artifact_id в save_report — стадия «сохранение» конвейера.",
    )


@server.tool()
async def save_report(
    artifact_id: Annotated[int, Field(description="id артефакта от analyze_data", ge=1)],
    title: Annotated[str | None, Field(description="Заголовок отчёта (по умолчанию «Погода: город»)")] = None,
) -> ReportSaved:
    """Стадия 3 «сохранение»: отчёт в SQLite + JSON-файл с блоком lineage.

    Принимает artifact_id от analyze_data. Пересчитывает хэш исходных данных и
    сверяет с зафиксированным при обработке (lineage.matches) — сохранение само
    по себе проверяет, что данные прошли цепочку без изменений.
    """
    saved = do_save(artifact_id, title)
    lin = saved["lineage"]
    return ReportSaved(
        report_id=saved["report_id"],
        title=saved["title"],
        file_path=saved["file_path"],
        lineage=Lineage(**lin),
        created_at_utc=saved["created_at_utc"],
        note=(
            "Отчёт сохранён в SQLite и файл. lineage.matches="
            f"{str(lin['matches']).lower()} — хэши исходных данных совпали, "
            "цепочка передача→обработка прошла без изменений."
            if lin["matches"]
            else "Отчёт сохранён, но хэши исходных данных НЕ совпали — проверьте целостность цепочки."
        ),
    )


@server.tool()
async def run_pipeline(
    city: Annotated[str, Field(description="Город, по которому гоняем конвейер")],
    use_llm: Annotated[bool, Field(description="Использовать LLM GLM на стадии обработки")] = True,
) -> PipelineRunResult:
    """АВТОМАТИЧЕСКАЯ цепочка: получение данных → обработка → сохранение одним вызовом.

    Сервер сам выполняет стадии fetch → analyze → save, передавая artifact_id
    между ними, и журналирует каждую стадию (вход/выход, статус, длительность).
    Проверка выполнения цепочки и передачи данных — inspect_run(run_id).
    """
    assert http_client is not None, "lifespan не запустился"
    run_id = create_run(city)
    stages: list[StageInfo] = []
    stage_no, stage_name, t0 = 0, None, time.perf_counter()

    def stage_done(pos: int, name: str, in_ref: str | None, out_ref: str, detail: str) -> StageInfo:
        dur = int(round((time.perf_counter() - t0) * 1000))
        record_stage(run_id, pos, name, "ok", in_ref, out_ref, detail, dur)
        info = StageInfo(position=pos, name=name, status="ok", input_ref=in_ref, output_ref=out_ref, duration_ms=dur, detail=detail)
        stages.append(info)
        return info

    try:
        stage_no, stage_name, t0 = 1, "fetch", time.perf_counter()
        raw_row = await do_fetch(http_client, city)
        raw_payload = artifact_payload(raw_row)
        stage_done(1, "fetch", None, f"artifact:{raw_row['id']}", f"город {raw_payload['city']['name']}")

        stage_no, stage_name, t0 = 2, "analyze", time.perf_counter()
        an_row = await do_analyze(http_client, raw_row["id"], use_llm)
        an_payload = artifact_payload(an_row)
        stage_done(2, "analyze", f"artifact:{raw_row['id']}", f"artifact:{an_row['id']}", f"вывод: {an_payload['summary_source']}")

        stage_no, stage_name, t0 = 3, "save", time.perf_counter()
        saved = do_save(an_row["id"], title=f"Погода: {raw_payload['city']['name']}")
        stage_done(3, "save", f"artifact:{an_row['id']}", f"report:{saved['report_id']}", Path(saved["file_path"]).name)

        finish_run(run_id, "ok")
        return PipelineRunResult(
            run_id=run_id,
            city=raw_payload["city"]["name"],
            status="ok",
            stages=stages,
            report_id=saved["report_id"],
            file_path=saved["file_path"],
            summary_text=an_payload["summary_text"],
            note=(
                "Автоцепочка выполнена: данные прошли стадии по artifact_id. "
                f"Проверка цепочки и передачи данных — inspect_run(run_id={run_id})."
            ),
        )
    except Exception as exc:
        if stage_name is not None:
            dur = int(round((time.perf_counter() - t0) * 1000))
            record_stage(run_id, stage_no, stage_name, "failed", None, None, str(exc), dur)
            stages.append(
                StageInfo(position=stage_no, name=stage_name, status="failed", input_ref=None, output_ref=None, duration_ms=dur, detail=str(exc))
            )
        finish_run(run_id, "failed", error=f"{stage_name}: {exc}")
        raise ToolError(
            f"Конвейер упал на стадии «{stage_name}»: {exc}. "
            f"Аудит запуска — inspect_run(run_id={run_id})."
        ) from exc


@server.tool()
async def inspect_run(
    run_id: Annotated[int, Field(description="id запуска от run_pipeline", ge=1)],
) -> RunInspection:
    """Аудит запуска: проверяет выполнение цепочки и передачу данных.

    Проверяет: статус запуска; порядок и статусы стадий (fetch → analyze → save);
    связность ссылок (выход стадии N == входу стадии N+1); виды артефактов;
    ЦЕЛОСТНОСТЬ ДАННЫХ (sha256 исходных данных == хэшу, зафиксированному при
    обработке — данные дошли без изменений); сохранённый отчёт (строка SQLite +
    файл на диске непустого размера).
    """
    with db_connect() as conn:
        run = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if run is None:
            raise ToolError(f"Запуск {run_id} не найден. Сначала run_pipeline.")
        stage_rows = conn.execute(
            "SELECT * FROM stages WHERE run_id = ? ORDER BY position", (run_id,)
        ).fetchall()

    stages = [
        StageInfo(
            position=s["position"], name=s["name"], status=s["status"],
            input_ref=s["input_ref"], output_ref=s["output_ref"],
            duration_ms=s["duration_ms"], detail=s["detail"],
        )
        for s in stage_rows
    ]
    checks: list[CheckResult] = []

    # 1. Статус запуска.
    checks.append(
        CheckResult(
            name="run_status",
            ok=run["status"] == "ok",
            detail=f"status={run['status']}" + (f", error={run['error']}" if run["error"] else ""),
        )
    )

    # 2. Порядок и статусы стадий.
    expected = ["fetch", "analyze", "save"]
    names = [s["name"] for s in stage_rows]
    statuses = [s["status"] for s in stage_rows]
    seq_ok = names == expected and bool(statuses) and all(st == "ok" for st in statuses)
    chain_view = " → ".join(f"{n}({st})" for n, st in zip(names, statuses)) or "стадий нет"
    checks.append(
        CheckResult(
            name="stage_sequence",
            ok=seq_ok,
            detail=f"стадии: {chain_view}; ожидается fetch → analyze → save, все ok",
        )
    )

    # 3. Связность ссылок: выход стадии N == вход стадии N+1.
    fetch_aid = parse_ref(stage_rows[0]["output_ref"]) if len(stage_rows) >= 1 else None
    analyze_aid = parse_ref(stage_rows[1]["output_ref"]) if len(stage_rows) >= 2 else None
    report_rid = parse_ref(stage_rows[2]["output_ref"]) if len(stage_rows) >= 3 else None
    if len(stage_rows) == 3:
        links_ok = (
            fetch_aid is not None
            and analyze_aid is not None
            and stage_rows[1]["input_ref"] == stage_rows[0]["output_ref"]
            and report_rid is not None
            and stage_rows[2]["input_ref"] == stage_rows[1]["output_ref"]
        )
        links_detail = (
            f"{stage_rows[0]['output_ref']} → analyze(вход {stage_rows[1]['input_ref']}) → "
            f"save(вход {stage_rows[2]['input_ref']}): ссылки связаны"
            if links_ok
            else f"ссылки НЕ связаны: {stage_rows[0]['output_ref']} / {stage_rows[1]['input_ref']} / {stage_rows[2]['input_ref']}"
        )
    else:
        links_ok = False
        links_detail = f"стадий меньше трёх ({len(stage_rows)}), связность не проверить"
    checks.append(CheckResult(name="chain_links", ok=links_ok, detail=links_detail))

    # 4. Виды артефактов.
    a_fetch = load_artifact(fetch_aid) if fetch_aid is not None else None
    a_analyze = load_artifact(analyze_aid) if analyze_aid is not None else None
    kinds_ok = (
        a_fetch is not None and a_analyze is not None
        and a_fetch["kind"] == "raw_weather" and a_analyze["kind"] == "weather_analysis"
    )
    kinds_detail = (
        f"fetch → {a_fetch['kind'] if a_fetch else 'нет'}, analyze → {a_analyze['kind'] if a_analyze else 'нет'}"
    )
    checks.append(CheckResult(name="artifact_kinds", ok=kinds_ok, detail=kinds_detail))

    # 5. Целостность данных: хэш исходного артефакта == хэшу при обработке.
    if a_fetch is not None and a_analyze is not None:
        analysis = artifact_payload(a_analyze)
        hash_now = hashlib.sha256(a_fetch["payload"].encode()).hexdigest()
        integrity_ok = (
            analysis.get("source_artifact_id") == a_fetch["id"]
            and analysis.get("input_payload_hash") == hash_now
        )
        integrity_detail = (
            f"sha256 исходных данных совпадает с зафиксированным при обработке — "
            f"данные дошли fetch → analyze без изменений"
            if integrity_ok
            else "хэши НЕ совпадают: данные изменились между стадиями (или ссылка неверна)"
        )
    else:
        integrity_ok = False
        integrity_detail = "артефакты недоступны — целостность не проверить"
    checks.append(CheckResult(name="data_integrity", ok=integrity_ok, detail=integrity_detail))

    # 6. Отчёт: строка SQLite + файл на диске.
    report_ok, report_detail = False, "стадия save не дошла до отчёта"
    if report_rid is not None:
        with db_connect() as conn:
            rep = conn.execute("SELECT * FROM reports WHERE id = ?", (report_rid,)).fetchone()
        if rep is None:
            report_detail = f"отчёт report:{report_rid} отсутствует в SQLite"
        else:
            path = Path(rep["file_path"])
            file_ok = path.exists() and path.stat().st_size > 0
            report_ok = file_ok and rep["artifact_id"] == analyze_aid
            report_detail = (
                f"отчёт {report_rid}: строка в SQLite, файл {path.name} "
                f"({path.stat().st_size if path.exists() else 0} байт), ссылка на артефакт анализа корректна"
                if report_ok
                else f"проблема с отчётом {report_rid}: файл={'есть' if file_ok else 'нет'}, "
                     f"artifact_id={rep['artifact_id']} (ожидался {analyze_aid})"
            )
    checks.append(CheckResult(name="report_saved", ok=report_ok, detail=report_detail))

    all_ok = all(c.ok for c in checks)
    conclusion = (
        "Цепочка выполнена полностью: данные прошли fetch → analyze → save без изменений "
        "(sha256 совпадает), отчёт сохранён в SQLite и файл."
        if all_ok
        else "Цепочка НЕ прошла аудит: провалены проверки — " + ", ".join(c.name for c in checks if not c.ok)
    )
    return RunInspection(
        run_id=run_id, city=run["city"], status=run["status"],
        all_ok=all_ok, stages=stages, checks=checks, conclusion=conclusion,
    )


@server.tool()
async def list_reports() -> ReportList:
    """Последние сохранённые отчёты конвейера (новые сверху)."""
    with db_connect() as conn:
        rows = conn.execute("SELECT * FROM reports ORDER BY id DESC LIMIT 10").fetchall()
    return ReportList(
        reports=[
            ReportInfo(
                report_id=r["id"], title=r["title"], city=r["city"],
                file_path=r["file_path"], created_at_utc=r["created_at_utc"],
            )
            for r in rows
        ]
    )


if __name__ == "__main__":
    # Логи SDK идут в stderr — на Windows он по умолчанию не UTF-8, из-за чего
    # кириллица превращается в «кракозябры». stdout не трогаем: он занят JSON-RPC.
    if sys.platform == "win32" and sys.stderr.encoding and sys.stderr.encoding.lower() not in ("utf-8", "utf8"):
        sys.stderr.reconfigure(encoding="utf-8")
    server.run("stdio")
