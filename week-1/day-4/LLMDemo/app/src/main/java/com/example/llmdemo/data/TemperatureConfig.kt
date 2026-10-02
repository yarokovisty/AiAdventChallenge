package com.example.llmdemo.data

/**
 * Одна «точка температуры» — набор sampling-параметров, применяемых к одному и тому же запросу.
 *
 * - [temperature] — основной параметр креативности/случайности (на Z.ai диапазон [0.0, 1.0]).
 * - [topP]        — опциональный top_p; поднимаем до 1.0 только в «хаос»-режиме,
 *                   чтобы приблизить эффект temperature=1.2, которую Z.ai не принимает.
 */
data class TemperatureConfig(
    val label: String,
    val description: String,
    val temperature: Double,
    val topP: Double? = null,
) {
    /** Краткая техсводка применённых параметров (для подписи карточки). */
    val paramsSummary: String
        get() = buildList {
            add("temperature=$temperature")
            if (topP != null) add("top_p=$topP")
        }.joinToString(" · ")
}

/** Результат одного прогона: видимый ответ + число токенов ответа. */
data class AskResult(
    val content: String,
    val completionTokens: Int,
)

/**
 * Три точки температуры для сравнения.
 *
 * Условие задания — 0 / 0.7 / 1.2. Но Z.ai GLM ограничивает temperature диапазоном [0.0, 1.0],
 * поэтому третью точку приближаем как temperature=1.0 + top_p=1.0 (максимальный «хаос»,
 * который допускает API). В OpenAI-совместимых API потолок 2.0 — там 1.2 было бы валидным.
 */
object TemperatureConfigs {
    const val DEFAULT_PROMPT =
        "Придумай короткий слоган (до 6 слов) для уютной городской кофейни. " +
            "В самом конце отдельной строкой напиши, сколько будет 2 + 2."

    /** Сколько раз прогоняем каждую температуру — чтобы наглядно увидеть разнообразие. */
    const val RUNS_PER_TEMPERATURE = 3

    val all: List<TemperatureConfig> = listOf(
        TemperatureConfig(
            label = "temperature = 0.0",
            description = "Детерминизм: модель всегда берёт самый вероятный токен. " +
                "Ждём почти идентичные ответы между прогонами.",
            temperature = 0.0,
        ),
        TemperatureConfig(
            label = "temperature = 0.7",
            description = "Баланс точности и креативности — рекомендуемый «рабочий» режим.",
            temperature = 0.7,
        ),
        TemperatureConfig(
            label = "temperature = 1.0 + top_p = 1.0  (≈ 1.2)",
            description = "Максимальный хаос, доступный на Z.ai. Приближение к условию 1.2 " +
                "(Z.ai режет temperature на 1.0). Ждём самые разные и рискованные ответы.",
            temperature = 1.0,
            topP = 1.0,
        ),
    )
}
