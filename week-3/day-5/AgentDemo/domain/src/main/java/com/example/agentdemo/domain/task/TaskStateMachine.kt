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
 * Переходы не просто структурные — два из них закрыты «шлюзами» (guards),
 * которые нельзя обойти:
 * - `planning → execution` открывается только после [approvePlan]
 *   ([TaskState.planApproved]) — «нельзя делать реализацию до утверждённого плана»;
 * - `validation → done` открывается только после [markValidationPassed]
 *   ([TaskState.validationPassed]) — «нельзя делать финал без валидации».
 *
 * Любой переход проходит через [tryTransitionTo], который возвращает
 * [TransitionResult]: [TransitionResult.Allowed] с новым состоянием либо
 * [TransitionResult.Rejected] с причиной (пауза / перепрыгивание этапа / закрытый
 * шлюз). Так ассистент физически не может «перепрыгнуть» этап, а UI показывает
 * человекочитаемый отказ вместо тихого игнора.
 *
 * Пауза — это не отдельный этап, а флаг [TaskState.isPaused] поверх любого
 * этапа: задачу можно приостановить на planning/execution/validation и затем
 * продолжить с того же шага. На паузе переходы отклоняются — сначала [resume].
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

    /** Этапы, в которые разрешён переход из [stage] (для подсказок в UI и промпте). */
    fun allowedTargets(stage: TaskStage): Set<TaskStage> = allowed(stage)

    /** Можно ли двинуть задачу на следующий этап (не пауза и не [TaskStage.DONE]). */
    fun canAdvance(state: TaskState): Boolean =
        !state.isPaused && state.stage.advanceTarget != null

    /** Можно ли вернуть задачу с проверки на доработку. */
    fun canRollback(state: TaskState): Boolean =
        !state.isPaused && TaskStage.EXECUTION in allowed(state.stage) &&
            state.stage == TaskStage.VALIDATION

    /**
     * Попытка перейти на следующий этап вперёд (planning→execution→validation→done).
     * Может быть отклонена закрытым шлюзом — см. [tryTransitionTo].
     */
    fun tryAdvance(state: TaskState): TransitionResult {
        val target = state.stage.advanceTarget
            ?: return TransitionResult.Rejected("Задача уже на финальном этапе — двигаться вперёд некуда.")
        return tryTransitionTo(state, target)
    }

    /** Попытка вернуть задачу с проверки на доработку (validation → execution). */
    fun tryRollback(state: TaskState): TransitionResult =
        tryTransitionTo(state, TaskStage.EXECUTION)

    /**
     * Единственная точка смены этапа. Проверяет по порядку: не на паузе ли задача,
     * легален ли переход по [TRANSITIONS] (иначе это «перепрыгивание»), открыт ли
     * шлюз этапа. Любой провал — [TransitionResult.Rejected] с причиной; успех —
     * [TransitionResult.Allowed] с новым состоянием (дефолтные шаг/действие нового
     * этапа, событие в журнале).
     */
    fun tryTransitionTo(state: TaskState, target: TaskStage): TransitionResult {
        if (state.isPaused) {
            return TransitionResult.Rejected(
                "Задача на паузе — сначала продолжите её, затем меняйте этап.",
            )
        }
        val targets = allowed(state.stage)
        if (target !in targets) {
            val reason = if (targets.isEmpty()) {
                "Задача на этапе «${state.stage.title}» завершена — переходить больше некуда."
            } else {
                "Нельзя перейти «${state.stage.title}» → «${target.title}», минуя этапы. " +
                    "Из «${state.stage.title}» допустим переход в: ${targets.joinToString { it.title }}."
            }
            return TransitionResult.Rejected(reason)
        }
        gateRejection(state, target)?.let { return TransitionResult.Rejected(it) }
        return TransitionResult.Allowed(transitionTo(state, target))
    }

    /**
     * Утвердить план — открывает шлюз перехода к реализации. Имеет смысл только на
     * этапе [TaskStage.PLANNING]; повторное утверждение ничего не меняет.
     */
    fun approvePlan(state: TaskState): TaskState {
        if (state.planApproved || state.stage != TaskStage.PLANNING) return state
        return state.copy(
            planApproved = true,
            log = state.log + "План утверждён — открыт переход «${TaskStage.PLANNING.title}» → «${TaskStage.EXECUTION.title}».",
        )
    }

    /**
     * Отметить валидацию пройденной — открывает шлюз завершения. Имеет смысл только
     * на этапе [TaskStage.VALIDATION]; повторная отметка ничего не меняет.
     */
    fun markValidationPassed(state: TaskState): TaskState {
        if (state.validationPassed || state.stage != TaskStage.VALIDATION) return state
        return state.copy(
            validationPassed = true,
            log = state.log + "Валидация пройдена — открыт переход «${TaskStage.VALIDATION.title}» → «${TaskStage.DONE.title}».",
        )
    }

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
     * Проверка шлюзов перехода. Возвращает причину отказа, если шлюз закрыт, или
     * `null`, если переход шлюзом не ограничен (или шлюз открыт).
     */
    private fun gateRejection(state: TaskState, target: TaskStage): String? = when {
        state.stage == TaskStage.PLANNING && target == TaskStage.EXECUTION && !state.planApproved ->
            "Нельзя приступать к реализации: план ещё не утверждён. " +
                "Сначала утвердите план — переход «${TaskStage.PLANNING.title}» → «${TaskStage.EXECUTION.title}» открыт только после этого."
        target == TaskStage.DONE && !state.validationPassed ->
            "Нельзя завершить задачу: результат не прошёл валидацию. " +
                "Сначала отметьте валидацию пройденной — переход к «${TaskStage.DONE.title}» открыт только после этого."
        else -> null
    }

    /**
     * Фактическая смена этапа (уже проверенная в [tryTransitionTo]). Подставляет
     * дефолтные шаг/действие нового этапа и пишет событие в журнал. Возврат на
     * доработку (validation → execution) сбрасывает [TaskState.validationPassed]:
     * прежняя валидация уже недействительна, финал снова закрыт шлюзом.
     * Остаётся приватным и бросает исключение — это страховка от логической ошибки.
     */
    private fun transitionTo(state: TaskState, target: TaskStage): TaskState {
        require(!state.isPaused) { "Нельзя менять этап на паузе — сначала продолжите задачу" }
        require(target in allowed(state.stage)) {
            "Недопустимый переход: ${state.stage.title} → ${target.title}"
        }
        val defaults = defaultsFor(target)
        val isRework = state.stage == TaskStage.VALIDATION && target == TaskStage.EXECUTION
        val event = if (isRework) {
            "Возврат на доработку: ${state.stage.title} → ${target.title}. Валидацию нужно пройти заново."
        } else {
            "Переход: ${state.stage.title} → ${target.title}."
        }
        return state.copy(
            stage = target,
            currentStep = defaults.step,
            expectedAction = defaults.expectedAction,
            validationPassed = if (isRework) false else state.validationPassed,
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
