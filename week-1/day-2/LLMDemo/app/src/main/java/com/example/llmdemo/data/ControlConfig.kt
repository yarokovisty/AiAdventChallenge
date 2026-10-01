package com.example.llmdemo.data

/**
 * Один «уровень контроля» ответа: какие параметры API применяются к одному и тому же запросу.
 *
 * - [systemPrompt]  — явная инструкция модели (в т.ч. описание формата / условие завершения)
 * - [maxTokens]     — ограничение длины ответа (max_tokens)
 * - [stop]          — стоп-последовательности (stop), до 4 строк
 * - [jsonFormat]    — включить response_format = json_object
 */
data class ControlConfig(
    val label: String,
    val description: String,
    val systemPrompt: String? = null,
    val maxTokens: Int? = null,
    val stop: List<String>? = null,
    val jsonFormat: Boolean = false,
) {
    /** Краткая сводка применённых параметров контроля для отображения в UI. */
    val paramsSummary: String
        get() {
            val parts = buildList {
                if (jsonFormat) add("response_format=json_object")
                if (maxTokens != null) add("max_tokens=$maxTokens")
                if (stop != null) add("stop=${stop.joinToString(prefix = "[", postfix = "]") { "\"$it\"" }}")
                if (systemPrompt != null) add("system-инструкция")
            }
            return if (parts.isEmpty()) "параметры контроля: нет" else parts.joinToString(" · ")
        }
}

/** Результат одного запроса вместе с метаданными, по которым сравниваем варианты. */
data class AskResult(
    val content: String,
    val finishReason: String,
    val completionTokens: Int,
    val totalTokens: Int,
)

/** Пять уровней контроля одного и того же запроса — для наглядного сравнения. */
object ControlConfigs {
    const val DEFAULT_PROMPT = "Расскажи о преимуществах Kotlin для Android-разработки."

    val all: List<ControlConfig> = listOf(
        ControlConfig(
            label = "1. Baseline — без ограничений",
            description = "Запрос как есть, без единого параметра контроля.",
        ),
        ControlConfig(
            label = "2. Контроль формата",
            description = "response_format=json_object + явное описание структуры ответа.",
            systemPrompt = "Отвечай СТРОГО в формате JSON-объекта со следующими полями: " +
                "\"summary\" (строка — суть одним предложением), " +
                "\"advantages\" (массив строк — список преимуществ). " +
                "Не добавляй markdown, пояснения или любой текст вне JSON.",
            jsonFormat = true,
        ),
        ControlConfig(
            label = "3. Контроль длины",
            description = "max_tokens=64 — жёсткий лимит длины ответа (ждём обрыв по length).",
            maxTokens = 64,
        ),
        ControlConfig(
            label = "4. Условие завершения (stop)",
            description = "stop=[\"[STOP]\"] + инструкция завершить ответ этим маркером.",
            systemPrompt = "Дай развёрнутый ответ. Как только закончишь основную мысль, " +
                "напиши на отдельной строке маркер [STOP] и ничего не пиши после него.",
            stop = listOf("[STOP]"),
        ),
        ControlConfig(
            label = "5. Всё вместе",
            description = "Формат (инструкция) + max_tokens=120 + stop=[\"[STOP]\"].",
            systemPrompt = "Ответь коротким нумерованным списком пунктов. " +
                "В самом конце поставь на отдельной строке маркер [STOP].",
            maxTokens = 120,
            stop = listOf("[STOP]"),
        ),
    )
}
