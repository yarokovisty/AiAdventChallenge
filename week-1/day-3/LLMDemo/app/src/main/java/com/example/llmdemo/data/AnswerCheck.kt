package com.example.llmdemo.data

/**
 * Эвристическая проверка итогового ответа модели.
 *
 * Ответ приходит свободным текстом. Итог ищем по убыванию надёжности:
 * 1. явный маркер «Ответ: N»;
 * 2. формулировка «N раз» (для задач на подсчёт);
 * 3. запасной вариант — последнее число в тексте.
 *
 * Шаг 3 нельзя делать первым: у способа «самопромпт» ответ часто заканчивается
 * перечислением вида «… — кларнет — 1», и последнее число (1) — не итог.
 * Это не строгий парсер, но для демо-задачи с числовым ответом его достаточно.
 */
object AnswerCheck {
    private val answerMarker = Regex("""ответ\W{0,5}(\d+)""", RegexOption.IGNORE_CASE)
    private val timesMarker = Regex("""(\d+)\s*раз""", RegexOption.IGNORE_CASE)
    private val anyNumber = Regex("""\d+""")

    /** Извлекает итоговое число ответа или null, если чисел нет. */
    fun extractAnswer(text: String): String? {
        answerMarker.findAll(text).lastOrNull()?.let { return it.groupValues[1] }
        timesMarker.findAll(text).lastOrNull()?.let { return it.groupValues[1] }
        return anyNumber.findAll(text).lastOrNull()?.value
    }

    /** Совпадает ли извлечённый ответ с эталоном. */
    fun matches(text: String, reference: String): Boolean =
        extractAnswer(text) == reference
}
