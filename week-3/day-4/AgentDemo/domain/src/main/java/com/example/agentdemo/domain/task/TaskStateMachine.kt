package com.example.agentdemo.domain.task

/**
 * Конечный автомат задачи — вся логика переходов в одном чистом месте.
 *
 * Не хранит состояние и не делает ввод-вывод: принимает текущий [TaskState] и
 * возвращает новый. Разрешённые переходы заданы таблицей [TRANSITIONS] —
 * именно это и формализует жизненный цикл задачи:
 *
 * ```
 * planning ──► execution ──► validation ──► done
 *                   ▲             │
 *                   └─────────────┘  (возврат на доработку)
 * ```
 *
 * Пауза — это не отдельный этап, а флаг [TaskState.isPaused] поверх любого
 * этапа: задачу можно приостановить на planning/execution/validation и затем
 * продолжить с того же шага. На паузе переходы запрещены — сначала [resume].
 */
object TaskStateMachine {

    /** Таблица разрешённых переходов между этапами. Пустое множество у [TaskStage.DONE]. */
    private val TRANSITIONS: Map<TaskStage, Set<TaskStage>> = mapOf(
        TaskStage.PLANNING to setOf(TaskStage.EXECUTION),
        TaskStage.EXECUTION to setOf(TaskStage.VALIDATION),
        TaskStage.VALIDATION to setOf(TaskStage.DONE, TaskStage.EXECUTION),
        TaskStage.DONE to emptySet(),
    )

    /** Создаёт новую задачу на этапе [TaskStage.PLANNING] с дефолтами этапа. */
    fun start(goal: String): TaskState {
        val defaults = defaultsFor(TaskStage.PLANNING)
        return TaskState(
            goal = goal.trim(),
            stage = TaskStage.PLANNING,
            currentStep = defaults.step,
            expectedAction = defaults.expectedAction,
            isPaused = false,
            log = listOf("Задача начата на этапе «${TaskStage.PLANNING.title}»."),
        )
    }

    /** Можно ли двинуть задачу на следующий этап (не пауза и не [TaskStage.DONE]). */
    fun canAdvance(state: TaskState): Boolean =
        !state.isPaused && state.stage.advanceTarget != null

    /** Можно ли вернуть задачу с проверки на доработку. */
    fun canRollback(state: TaskState): Boolean =
        !state.isPaused && TaskStage.EXECUTION in allowed(state.stage) &&
            state.stage == TaskStage.VALIDATION

    /** Переход на следующий этап вперёд (planning→execution→validation→done). */
    fun advance(state: TaskState): TaskState {
        val target = state.stage.advanceTarget ?: return state
        return transitionTo(state, target)
    }

    /** Возврат с проверки на доработку (validation → execution). */
    fun rollback(state: TaskState): TaskState =
        if (canRollback(state)) transitionTo(state, TaskStage.EXECUTION) else state

    /** Приостановить задачу на текущем этапе (повторная пауза ничего не меняет). */
    fun pause(state: TaskState): TaskState {
        if (state.isPaused) return state
        return state.copy(
            isPaused = true,
            log = state.log + "Пауза на этапе «${state.stage.title}» (шаг: ${state.currentStep}).",
        )
    }

    /** Продолжить задачу с того же шага, на котором её поставили на паузу. */
    fun resume(state: TaskState): TaskState {
        if (!state.isPaused) return state
        return state.copy(
            isPaused = false,
            log = state.log + "Продолжение с этапа «${state.stage.title}» (шаг: ${state.currentStep}).",
        )
    }

    /** Уточнить текущий шаг и ожидаемое действие, не меняя этап. */
    fun updateStep(state: TaskState, step: String, expectedAction: String): TaskState {
        val newStep = step.trim().ifBlank { state.currentStep }
        val newAction = expectedAction.trim().ifBlank { state.expectedAction }
        if (newStep == state.currentStep && newAction == state.expectedAction) return state
        return state.copy(
            currentStep = newStep,
            expectedAction = newAction,
            log = state.log + "Уточнён шаг на этапе «${state.stage.title}»: $newStep.",
        )
    }

    /**
     * Единственная точка смены этапа: проверяет легальность перехода по
     * [TRANSITIONS], подставляет дефолтные шаг/ожидаемое действие нового этапа и
     * пишет событие в журнал. Недопустимый переход — исключение (баг в UI-логике).
     */
    private fun transitionTo(state: TaskState, target: TaskStage): TaskState {
        require(!state.isPaused) { "Нельзя менять этап на паузе — сначала продолжите задачу" }
        require(target in allowed(state.stage)) {
            "Недопустимый переход: ${state.stage.title} → ${target.title}"
        }
        val defaults = defaultsFor(target)
        val isRework = state.stage == TaskStage.VALIDATION && target == TaskStage.EXECUTION
        val event = if (isRework) {
            "Возврат на доработку: ${state.stage.title} → ${target.title}."
        } else {
            "Переход: ${state.stage.title} → ${target.title}."
        }
        return state.copy(
            stage = target,
            currentStep = defaults.step,
            expectedAction = defaults.expectedAction,
            log = state.log + event,
        )
    }

    private fun allowed(stage: TaskStage): Set<TaskStage> = TRANSITIONS[stage].orEmpty()

    /** Дефолтные «текущий шаг» и «ожидаемое действие» для каждого этапа. */
    private fun defaultsFor(stage: TaskStage): StageDefaults = when (stage) {
        TaskStage.PLANNING -> StageDefaults(
            step = "Сформулировать план решения задачи",
            expectedAction = "Разбей задачу на конкретные шаги и предложи план.",
        )
        TaskStage.EXECUTION -> StageDefaults(
            step = "Выполнение шагов плана",
            expectedAction = "Выполняй шаги плана по порядку, фиксируй промежуточный результат.",
        )
        TaskStage.VALIDATION -> StageDefaults(
            step = "Проверка результата на соответствие цели",
            expectedAction = "Сверь результат с целью, найди пробелы; подтверди готовность или верни на доработку.",
        )
        TaskStage.DONE -> StageDefaults(
            step = "Задача завершена",
            expectedAction = "Задача закрыта — дополнительных действий не требуется.",
        )
    }

    private data class StageDefaults(val step: String, val expectedAction: String)
}
