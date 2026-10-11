package com.example.agentdemo.domain.task

/**
 * Этап задачи — состояние конечного автомата.
 *
 * Линейный жизненный цикл: [PLANNING] → [EXECUTION] → [VALIDATION] → [DONE].
 * На этапе проверки допустим возврат на доработку ([VALIDATION] → [EXECUTION]) —
 * так автомат остаётся формальным, но не строго однонаправленным. Разрешённые
 * переходы описаны в [TaskStateMachine], здесь — только сами состояния и их
 * человекочитаемые подписи.
 *
 * @param order порядковый номер этапа — задаёт движение «вперёд» в [advanceTarget].
 * @param title человекочитаемое название этапа (для UI и системного промпта).
 * @param slug латинский ключ этапа из условия (planning/execution/validation/done).
 */
enum class TaskStage(val order: Int, val title: String, val slug: String) {
    PLANNING(0, "Планирование", "planning"),
    EXECUTION(1, "Выполнение", "execution"),
    VALIDATION(2, "Проверка", "validation"),
    DONE(3, "Готово", "done");

    /** Следующий этап «вперёд» по порядку (или `null`, если это уже [DONE]). */
    val advanceTarget: TaskStage?
        get() = entries.firstOrNull { it.order == order + 1 }
}
