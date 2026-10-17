package com.example.agentdemo.domain.task

/**
 * Результат попытки перехода конечного автомата.
 *
 * Переход может быть отклонён — на паузе, при попытке перепрыгнуть этап или при
 * закрытом шлюзе (план не утверждён / валидация не пройдена). В этом случае важно
 * показать пользователю причину, а не молча проигнорировать действие и не уронить
 * приложение. Поэтому [TaskStateMachine.tryTransitionTo] возвращает либо [Allowed]
 * с новым состоянием, либо [Rejected] с человекочитаемой причиной отказа.
 */
sealed interface TransitionResult {

    /** Переход разрешён — [state] уже содержит новое состояние автомата. */
    data class Allowed(val state: TaskState) : TransitionResult

    /** Переход отклонён — [reason] объясняет, почему он сейчас недопустим. */
    data class Rejected(val reason: String) : TransitionResult
}
