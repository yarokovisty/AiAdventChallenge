"""
Week 4 · Day 5 — MCP-сервер «travel-kit»: финальная обработка сценария.

Этот файл — MCP-СЕРВЕР (транспорт stdio) для мульти-серверной оркестрации
дня 5. Если серверы дней 2–4 ходят во внешний мир (Open-Meteo, LLM GLM,
SQLite), то travel-kit — НОВЫЙ сервер без сети: чистые детерминированные
правила, которые замыкают длинный сценарий «поездка» на стороне клиента.

Три инструмента (вход — данные, которые агент получил с ДРУГИХ серверов):

  • recommend_clothing  — температура/ветер/условия → список вещей и нужен ли
                          зонт (правила по диапазонам);
  • build_checklist     — город, длительность поездки и сводка прогноза →
                          чек-лист сборов с категориями и причинами;
  • score_travel_weather— средняя температура/дождливые дни/ветер → оценка
                          погоды для поездки 0–100 с вердиктом и объяснением.

Смысл в архитектуре дня: инструменты разных серверов обмениваются данными
ЧЕРЕЗ АГЕНТА — координаты и агрегаты прогноза, полученные на серверах
weather/pipeline, агент подаёт на вход инструментам этого сервера. Сами
серверы друг о друге не знают.

Контракт — как в днях 2–4: inputSchema из аннотаций параметров,
outputSchema из pydantic-моделей возвращаемого типа, ошибки (ToolError) —
часть контракта.

Запуск (обычно его запускает agent_app.py как подпроцесс):
    python mcp_travel_kit_server.py
"""

import sys
from typing import Annotated

from pydantic import BaseModel, Field

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

# Условия, при которых зонт/дождевик обязателен. travel-kit получает condition
# строкой (как её отдаёт weather-сервер: «слабый дождь», «ливень», …), поэтому
# ищем подстроки — это дешевле и честнее точного соответствия кодам WMO.
RAIN_WORDS = ("дожд", "ливн", "морось", "гроза", "град", "снег", "снежн")

server = MCPServer(
    name="travel-kit",
    version="1.0.0",
    title="Travel Kit",
    instructions=(
        "Бытовой MCP-сервер для планирования поездки: рекомендации по одежде, "
        "чек-лист сборов и оценка погоды для поездки. Работает без сети по "
        "детерминированным правилам. Подавайте на вход данные прогноза, "
        "полученные от погодных серверов."
    ),
)


# --------------------------------------------------------------------------- #
# Модели результата (outputSchema строит MCP из аннотации возвращаемого типа).
# --------------------------------------------------------------------------- #


class ClothingAdvice(BaseModel):
    """Рекомендация по одежде для конкретной погоды."""

    temperature_c: float = Field(description="Температура, на которую даётся совет, °C")
    umbrella_needed: bool = Field(description="Нужен ли зонт или дождевик")
    items: list[str] = Field(description="Список вещей, которые стоит взять/надеть")
    notes: list[str] = Field(description="Пояснения: ветер, осадки, особенности дня")


class ChecklistItem(BaseModel):
    """Один пункт чек-листа сборов."""

    category: str = Field(description="Категория: документы, техника, здоровье, по погоде, общее")
    item: str = Field(description="Что взять")
    reason: str = Field(description="Почему именно это (зависит от прогноза и длительности)")


class TripChecklist(BaseModel):
    """Чек-лист сборов в поездку."""

    city: str = Field(description="Город поездки")
    trip_days: int = Field(description="Длительность поездки, дней")
    items: list[ChecklistItem] = Field(description="Пункты чек-листа по категориям")
    note: str = Field(description="Итоговое замечание к сборам")


class TravelScore(BaseModel):
    """Оценка погоды для поездки."""

    score: int = Field(description="Итоговая оценка 0–100 (больше — лучше)")
    verdict: str = Field(description="Вердикт одним словом: отлично / хорошо / сносно / не стоит")
    reasons: list[str] = Field(description="Объяснение оценки: что добавило и что сняло баллы")


# --------------------------------------------------------------------------- #
# Инструменты.
# --------------------------------------------------------------------------- #


@server.tool()
async def recommend_clothing(
    temperature_c: Annotated[float, Field(description="Температура воздуха, °C (из прогноза weather-сервера)", ge=-80, le=60)],
    condition: Annotated[str, Field(description="Условия погоды текстом, напр. «слабый дождь» (поле condition прогноза)")] = "",
    wind_kph: Annotated[float, Field(description="Скорость ветра, км/ч", ge=0, le=300)] = 0.0,
    rain_expected: Annotated[bool, Field(description="Явно указать, что осадки ожидаются (если condition не заполнен)")] = False,
) -> ClothingAdvice:
    """Советует, что надеть и взять с собой, по погоде.

    Детерминированные правила по диапазонам температуры + поправки на ветер
    и осадки. Входные данные удобно взять из прогноза weather-сервера
    (температура за нужный день, condition, wind_kph).
    """
    notes: list[str] = []
    items: list[str] = []

    if temperature_c >= 25:
        items += ["лёгкая дышащая одежда", "головной убор", "солнцезащитный крем", "бутылка воды"]
        notes.append("жарко: светлая одежда и головной убор обязательны")
    elif temperature_c >= 18:
        items += ["футболка", "лёгкая кофта на вечер"]
        notes.append("комфортно, вечером возможна прохлада")
    elif temperature_c >= 10:
        items += ["свитер или кофта", "лёгкая куртка"]
        notes.append("прохладно: два слоя одежды")
    elif temperature_c >= 0:
        items += ["тёплая куртка", "шапка и шарф"]
        notes.append("холодно: тёплая верхняя одежда")
    else:
        items += ["зимняя куртка", "термобельё", "шапка, шарф, перчатки"]
        notes.append("мороз: многослойная одежда")

    if wind_kph >= 30:
        items.append("непродуваемая ветровка")
        notes.append(f"сильный ветер {wind_kph:.0f} км/ч — нужна защита от ветра")
    elif wind_kph >= 20:
        items.append("ветровка")
        notes.append(f"ветер ощутимый ({wind_kph:.0f} км/ч) — ветровка не помешает")

    rainy = rain_expected or any(word in condition.lower() for word in RAIN_WORDS)
    if rainy:
        items.append("зонт или дождевик")
        if condition:
            notes.append(f"ожидаются осадки: {condition}")

    return ClothingAdvice(
        temperature_c=temperature_c,
        umbrella_needed=rainy,
        items=items,
        notes=notes,
    )


@server.tool()
async def build_checklist(
    city: Annotated[str, Field(description="Город поездки")],
    trip_days: Annotated[int, Field(description="Длительность поездки, дней (1–30)", ge=1, le=30)],
    avg_temperature_c: Annotated[float | None, Field(description="Средняя температура за поездку, °C (агрегат из analyze_data/pipeline)", ge=-80, le=60)] = None,
    rainy_days: Annotated[int, Field(description="Число дождливых дней из прогноза (0–30)", ge=0, le=30)] = 0,
) -> TripChecklist:
    """Собирает чек-лист сборов в поездку с учётом погоды и длительности.

    Базовые категории (документы, техника, здоровье) не зависят от погоды,
    категория «по погоде» — зависит: подавайте сюда агрегаты, которые вернул
    конвейер pipeline-сервера (avg_temperature_c, дождливые дни).
    """
    if rainy_days > trip_days:
        raise ToolError(
            f"дождливых дней ({rainy_days}) больше, чем длительность поездки ({trip_days}) — проверьте данные прогноза"
        )

    items: list[ChecklistItem] = [
        ChecklistItem(category="документы", item="паспорт", reason="база"),
        ChecklistItem(category="документы", item="билеты и брони", reason=f"поездка в {city}"),
        ChecklistItem(category="техника", item="зарядка и повербанк", reason="связь и навигация в дороге"),
        ChecklistItem(category="здоровье", item="аптечка-минимумка", reason="на случай недомогания"),
    ]

    if rainy_days > 0:
        items.append(ChecklistItem(category="по погоде", item="зонт или дождевик", reason=f"прогноз обещает осадки в {rainy_days} дн. из {trip_days}"))
    if avg_temperature_c is not None:
        if avg_temperature_c < 5:
            items.append(ChecklistItem(category="по погоде", item="тёплые вещи (шапка, перчатки)", reason=f"средняя температура {avg_temperature_c:.1f} °C"))
        elif avg_temperature_c < 15:
            items.append(ChecklistItem(category="по погоде", item="тёплая кофта и куртка", reason=f"средняя температура {avg_temperature_c:.1f} °C"))
        elif avg_temperature_c >= 23:
            items.append(ChecklistItem(category="по погоде", item="лёгкая одежда и крем от солнца", reason=f"средняя температура {avg_temperature_c:.1f} °C"))
    if trip_days >= 4:
        items.append(ChecklistItem(category="общее", item="запас носков/стирка в поездке", reason=f"поездка длинная, {trip_days} дн."))
    items.append(ChecklistItem(category="общее", item="бутылка воды и перекус", reason="дорога"))

    weather_note = "сводка прогноза не передана — пункты «по погоде» собраны по умолчанию"
    if avg_temperature_c is not None or rainy_days:
        weather_note = f"учтены средняя температура {avg_temperature_c if avg_temperature_c is not None else '—'} °C и {rainy_days} дождливых дн."

    return TripChecklist(city=city, trip_days=trip_days, items=items, note=weather_note)


@server.tool()
async def score_travel_weather(
    avg_temperature_c: Annotated[float, Field(description="Средняя температура за поездку, °C (агрегат конвейера)", ge=-80, le=60)],
    rainy_days: Annotated[int, Field(description="Число дождливых дней из прогноза", ge=0, le=30)] = 0,
    max_wind_kph: Annotated[float, Field(description="Максимальный ветер за поездку, км/ч", ge=0, le=300)] = 0.0,
) -> TravelScore:
    """Оценивает погоду для поездки: 0–100, вердикт и объяснение баллов.

    Идеальный диапазон 18–25 °C; отклонения, дождливые дни и сильный ветер
    снимают баллы. Подавайте агрегаты, которые вернул pipeline-сервер
    (analyze_data / run_pipeline), — тогда оценка считается по тем же
    данным, что и сохранённый отчёт.
    """
    score = 100.0
    reasons: list[str] = []

    if 18 <= avg_temperature_c <= 25:
        reasons.append(f"температура {avg_temperature_c:.1f} °C — в комфортном диапазоне 18–25 °C")
    elif avg_temperature_c > 25:
        penalty = (avg_temperature_c - 25) * 2.2
        score -= penalty
        reasons.append(f"жара {avg_temperature_c:.1f} °C: −{penalty:.0f} балл(ов)")
    else:
        penalty = (18 - avg_temperature_c) * 2.2
        score -= penalty
        reasons.append(f"прохлада {avg_temperature_c:.1f} °C: −{penalty:.0f} балл(ов)")

    if rainy_days:
        penalty = min(7 * rainy_days, 35)
        score -= penalty
        reasons.append(f"дождливых дней — {rainy_days}: −{penalty} балл(ов)")

    if max_wind_kph > 30:
        penalty = min((max_wind_kph - 30) * 0.8, 20)
        score -= penalty
        reasons.append(f"ветер до {max_wind_kph:.0f} км/ч: −{penalty:.0f} балл(ов)")

    score = max(0, min(100, round(score)))
    if score >= 80:
        verdict = "отлично"
    elif score >= 65:
        verdict = "хорошо"
    elif score >= 45:
        verdict = "сносно"
    else:
        verdict = "не стоит"

    return TravelScore(score=score, verdict=verdict, reasons=reasons)


if __name__ == "__main__":
    # Логи SDK идут в stderr — на Windows по умолчанию не UTF-8, из-за чего
    # кириллица превращается в «кракозябры». stdout не трогаем: он занят JSON-RPC.
    if sys.platform == "win32" and sys.stderr.encoding and sys.stderr.encoding.lower() not in ("utf-8", "utf8"):
        sys.stderr.reconfigure(encoding="utf-8")
    server.run("stdio")
