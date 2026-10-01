package com.example.llmdemo.data

/**
 * Один «способ рассуждения» над одной и той же задачей.
 *
 * Все четыре способа получают одинаковую задачу, но по-разному организуют работу модели:
 * от ответа без инструкций до многоролевой цепочки экспертов.
 */
enum class ReasoningStrategy(val label: String, val description: String) {
    DIRECT(
        label = "1. Прямой ответ",
        description = "Задача отправляется как есть, без каких-либо инструкций по рассуждению.",
    ),
    STEP_BY_STEP(
        label = "2. Пошагово",
        description = "К задаче добавлена инструкция «решай пошагово, рассуждай по шагам».",
    ),
    SELF_PROMPT(
        label = "3. Самопромпт",
        description = "Сначала модель сама составляет оптимальный промпт для задачи, затем решает им.",
    ),
    EXPERTS(
        label = "4. Группа экспертов",
        description = "Аналитик → инженер → критик: три роли последовательно уточняют решение.",
    ),
}

/** Один промежуточный шаг решения (раскрывающийся блок в карточке). */
data class ReasoningStep(
    val title: String,
    val text: String,
)

/** Итог одного способа: финальный ответ + промежуточные шаги + потраченные токены. */
data class StrategyResult(
    val strategy: ReasoningStrategy,
    val finalAnswer: String,
    val steps: List<ReasoningStep> = emptyList(),
    val completionTokens: Int = 0,
    val totalTokens: Int = 0,
    val isError: Boolean = false,
)

/** Задача, которую решаем всеми четырьмя способами, и её эталонный ответ для самопроверки. */
object ReasoningTask {
    const val DEFAULT_PROBLEM =
        "Сколько раз буква «р» встречается во фразе: " +
            "«Карл у Клары украл кораллы, а Клара у Карла украла кларнет»?"

    /** Проверено пошаговым перечислением: правильный ответ — 8. */
    const val REFERENCE_ANSWER = "8"
}
