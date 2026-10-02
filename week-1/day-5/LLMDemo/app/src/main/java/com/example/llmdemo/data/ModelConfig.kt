package com.example.llmdemo.data

/**
 * Одна модель в сравнении — её API-id, человекочитаемая подпись, «уровень» и цены.
 *
 * Цены — официальный прайс Z.ai за 1 млн токенов (USD), отдельно вход/выход.
 * По ним считаем теоретическую стоимость запроса (coding-подписка берёт плату иначе,
 * но для сравнения ресурсоёмкости считаем именно по токенам × прайс).
 */
data class ModelConfig(
    val id: String,
    val label: String,
    val level: String,
    val priceInputPerM: Double,
    val priceOutputPerM: Double,
) {
    /** Короткая сводка цены для подписи карточки. */
    val priceSummary: String
        get() = if (priceInputPerM == 0.0 && priceOutputPerM == 0.0) {
            "бесплатно"
        } else {
            "$$priceInputPerM / $$priceOutputPerM за 1M ток. (вход/выход)"
        }
}

/** Результат одного прогона: ответ + полный расход токенов + время ответа. */
data class AskResult(
    val content: String,
    val promptTokens: Int,
    val completionTokens: Int,
    val totalTokens: Int,
    val latencyMs: Long,
)

/**
 * Каталог моделей для сравнения и промпт-ловушка.
 *
 * Задание — «4 модели GPT разного уровня». Курс идёт на Z.ai/GLM, поэтому берём линейку
 * GLM как замену тиров GPT. Платёжный эндпоинт у пользователя закрыт (нет баланса), а
 * coding-эндпоинт даёт доступ к целой лестнице моделей — на нём и сравниваем.
 */
object ModelsCatalog {
    /**
     * Промпт-ловушка (Cognitive Reflection Test, «бита и мяч»).
     * Интуитивный неверный ответ — 10 центов; верный — 5 центов.
     * Последняя строка просит одно число — упрощает автодетект правильности.
     */
    const val DEFAULT_PROMPT =
        "Бейсбольная бита и мяч вместе стоят 1 доллар 10 центов. " +
            "Бита стоит на 1 доллар дороже мяча. Сколько стоит мяч? " +
            "В конце ответа отдельной строкой напиши только число — сколько это центов."

    /** 4 модели по возрастанию уровня и цены. */
    val all: List<ModelConfig> = listOf(
        ModelConfig(
            id = "glm-4.5-flash",
            label = "GLM-4.5-Flash",
            level = "Уровень 1 · лёгкая, бесплатная",
            priceInputPerM = 0.0,
            priceOutputPerM = 0.0,
        ),
        ModelConfig(
            id = "glm-4.5-air",
            label = "GLM-4.5-Air",
            level = "Уровень 2 · компактная",
            priceInputPerM = 0.20,
            priceOutputPerM = 1.10,
        ),
        ModelConfig(
            id = "glm-4.6",
            label = "GLM-4.6",
            level = "Уровень 3 · средняя",
            priceInputPerM = 0.60,
            priceOutputPerM = 2.20,
        ),
        ModelConfig(
            id = "glm-5.3",
            label = "GLM-5.3",
            level = "Уровень 4 · флагман",
            priceInputPerM = 1.40,
            priceOutputPerM = 4.40,
        ),
    )

    /**
     * Эвристика правильности для ловушки «бита и мяч».
     * Берём последнее число из ответа: 5 → верно, 10 → попал в ловушку, иначе null.
     */
    fun isCorrect(answer: String): Boolean? {
        val numbers = Regex("""\d+""").findAll(answer).map { it.value }.toList()
        val last = numbers.lastOrNull()?.toIntOrNull() ?: return null
        return when (last) {
            5 -> true
            10 -> false
            else -> null
        }
    }
}
