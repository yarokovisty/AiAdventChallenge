"""
Week 4 · Day 2 — Собственный MCP-сервер вокруг внешнего API (Open-Meteo).

Задание: реализовать свой MCP-сервер вокруг API, описать параметры инструмента
и возвращаемый результат, подключить инструмент к агенту и показать вызов
из приложения.

Этот файл — MCP-СЕРВЕР (транспорт stdio). Он оборачивает публичное API
Open-Meteo (https://open-meteo.com) — бесплатное, без ключей — и экспонирует
два инструмента:

  • geocode_city         — имя города → координаты и часовой пояс (Geocoding API);
  • get_weather_forecast — координаты → текущая погода + прогноз (Forecast API).

Контракт каждого инструмента формализован схемами, которые сервер сам публикует
в tools/list (клиент получает их при подключении):

  • вход  — inputSchema:  строится из аннотаций типов параметров
           (Annotated + Field(description/ge/le) → JSON Schema);
  • выход — outputSchema: строится из pydantic-модели возвращаемого значения
           (каждое поле описано через Field(description=...)).

Сервер не просто «проксирует» API: он скрывает детали внешнего формата
(плоские массивы daily), обогащает ответ человекочитаемой расшифровкой кодов
погоды WMO и возвращает нормализованную структуру.

Запуск (обычно его запускает агентское приложение agent_app.py как подпроцесс):
    python mcp_weather_server.py
"""

import logging
from typing import Annotated

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

# Человекочитаемые расшифровки кодов погоды WMO — сервер «обогащает» сырой API:
# клиент (и LLM) получает готовый текст, а не магическое число.
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


# --------------------------------------------------------------------------- #
# Модели результата. Из аннотации возвращаемого типа MCP-сервер автоматически
# строит outputSchema — формальное описание возвращаемого результата.
# --------------------------------------------------------------------------- #


class CityLocation(BaseModel):
    """Найденный город — лучшее совпадение по имени."""

    name: str = Field(description="Название города (на языке запроса)")
    country: str | None = Field(description="Страна", default=None)
    region: str | None = Field(description="Регион / область", default=None)
    latitude: float = Field(description="Широта, градусы (подать в get_weather_forecast)")
    longitude: float = Field(description="Долгота, градусы (подать в get_weather_forecast)")
    timezone: str | None = Field(description="Часовой пояс IANA, напр. Europe/Moscow", default=None)
    population: int | None = Field(description="Население, чел. (если известно)", default=None)


class CurrentWeather(BaseModel):
    """Погода прямо сейчас в запрошенной точке."""

    observed_at: str = Field(description="Время наблюдения, локальное для точки (ISO-8601)")
    temperature_c: float = Field(description="Температура воздуха на 2 м, °C")
    feels_like_c: float = Field(description="Ощущаемая температура, °C")
    humidity_pct: int = Field(description="Относительная влажность, %")
    precipitation_mm: float = Field(description="Осадки за последний час, мм")
    wind_kph: float = Field(description="Скорость ветра на 10 м, км/ч")
    weather_code: int = Field(description="Код погоды WMO (0 — ясно, 61 — дождь и т.д.)")
    condition: str = Field(description="Расшифровка кода погоды по-русски")


class DailyForecast(BaseModel):
    """Прогноз погоды на один день."""

    date: str = Field(description="Дата, YYYY-MM-DD")
    weather_code: int = Field(description="Код погоды WMO")
    condition: str = Field(description="Расшифровка кода погоды по-русски")
    temp_max_c: float = Field(description="Максимальная температура, °C")
    temp_min_c: float = Field(description="Минимальная температура, °C")
    precipitation_mm: float = Field(description="Суммарные осадки за день, мм")
    precipitation_probability_pct: int | None = Field(
        description="Максимальная вероятность осадков за день, %", default=None
    )


class WeatherForecast(BaseModel):
    """Текущая погода и прогноз по дням для точки с координатами."""

    location: str = Field(description="Точка запроса, напр. «59.94°N, 30.31°E»")
    timezone: str = Field(description="Часовой пояс точки (auto по координатам)")
    current: CurrentWeather = Field(description="Погода прямо сейчас")
    daily: list[DailyForecast] = Field(description="Прогноз по дням, начиная с сегодня")


# --------------------------------------------------------------------------- #
# Сервер и инструменты.
# --------------------------------------------------------------------------- #

server = MCPServer(
    name="open-meteo-weather",
    version="1.0.0",
    title="Open-Meteo Weather",
    instructions=(
        "Погодный MCP-сервер поверх API Open-Meteo. "
        "Чтобы узнать погоду в городе по названию, сначала вызовите geocode_city, "
        "затем передайте полученные latitude/longitude в get_weather_forecast."
    ),
)


@server.tool()
async def geocode_city(
    city: Annotated[str, Field(description="Название города, напр. «Санкт-Петербург» или «Paris»")],
    language: Annotated[str, Field(description="Язык названия города в ответе: ru, en, de, …")] = "ru",
) -> CityLocation:
    """Ищет город по имени и возвращает его координаты и часовой пояс.

    Вызывать ПЕРВЫМ, когда нужна погода в городе по названию: поля
    latitude/longitude результата подаются на вход инструмента
    get_weather_forecast. Если город не найден, возвращает ошибку — тогда
    стоит проверить написание или попробовать английский вариант имени.
    """
    params = {"name": city, "count": 1, "language": language, "format": "json"}
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as http:
            resp = await http.get(GEOCODING_URL, params=params)
            resp.raise_for_status()
            payload = resp.json()
    except httpx.HTTPError as exc:
        raise ToolError(f"Geocoding API Open-Meteo недоступен: {exc}") from exc

    results = payload.get("results") or []
    if not results:
        raise ToolError(
            f"Город «{city}» не найден. Проверьте написание или попробуйте английский вариант."
        )

    top = results[0]
    return CityLocation(
        name=top.get("name", city),
        country=top.get("country"),
        region=top.get("admin1"),
        latitude=top["latitude"],
        longitude=top["longitude"],
        timezone=top.get("timezone"),
        population=top.get("population"),
    )


@server.tool()
async def get_weather_forecast(
    latitude: Annotated[float, Field(description="Широта точки, ° (из geocode_city)", ge=-90, le=90)],
    longitude: Annotated[float, Field(description="Долгота точки, ° (из geocode_city)", ge=-180, le=180)],
    days: Annotated[int, Field(description="Число дней прогноза, 1–7", ge=1, le=7)] = 1,
) -> WeatherForecast:
    """Возвращает текущую погоду и прогноз по дням для точки с координатами.

    Координаты удобно получить инструментом geocode_city. Ответ содержит
    температуру, ощущаемую температуру, осадки, ветер, вероятность осадков
    и человекочитаемую расшифровку состояния (condition) — по ней можно
    решать, нужен ли зонт.
    """
    params = {
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
                "weather_code",
                "temperature_2m_max",
                "temperature_2m_min",
                "precipitation_sum",
                "precipitation_probability_max",
            ]
        ),
        "timezone": "auto",
        "forecast_days": days,
    }
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as http:
            resp = await http.get(FORECAST_URL, params=params)
            resp.raise_for_status()
            payload = resp.json()
    except httpx.HTTPError as exc:
        raise ToolError(f"Forecast API Open-Meteo недоступен: {exc}") from exc

    if isinstance(payload.get("error"), dict):  # Open-Meteo отдаёт ошибки с полем reason
        reason = payload["error"].get("reason") or payload["error"]
        raise ToolError(f"Forecast API вернул ошибку: {reason}")

    current = payload["current"]
    daily = payload["daily"]
    probability = daily.get("precipitation_probability_max") or []

    code = int(current["weather_code"])
    return WeatherForecast(
        location=(
            f"{abs(latitude):.2f}°{'N' if latitude >= 0 else 'S'}, "
            f"{abs(longitude):.2f}°{'E' if longitude >= 0 else 'W'}"
        ),
        timezone=payload.get("timezone", "unknown"),
        current=CurrentWeather(
            observed_at=current["time"],
            temperature_c=current["temperature_2m"],
            feels_like_c=current["apparent_temperature"],
            humidity_pct=current["relative_humidity_2m"],
            precipitation_mm=current["precipitation"],
            wind_kph=current["wind_speed_10m"],
            weather_code=code,
            condition=describe_code(code),
        ),
        daily=[
            DailyForecast(
                date=date,
                weather_code=int(day_code),
                condition=describe_code(int(day_code)),
                temp_max_c=t_max,
                temp_min_c=t_min,
                precipitation_mm=precip,
                precipitation_probability_pct=probability[i] if i < len(probability) else None,
            )
            for i, (date, day_code, t_max, t_min, precip) in enumerate(
                zip(
                    daily["time"],
                    daily["weather_code"],
                    daily["temperature_2m_max"],
                    daily["temperature_2m_min"],
                    daily["precipitation_sum"],
                )
            )
        ],
    )


if __name__ == "__main__":
    # stdio-транспорт: stdout занят JSON-RPC, поэтому никаких print'ов —
    # сервер молчит в протокол и просто работает.
    server.run("stdio")
