package com.example.agentdemo.domain.context

/**
 * Персистентное состояние управления контекстом: выбранная стратегия и
 * вспомогательные данные стратегий (факты, ветки). Переживает перезапуск
 * приложения вместе с историей диалога.
 */
data class ContextState(
    val strategy: ContextStrategy = ContextStrategy.SLIDING_WINDOW,
    val facts: Facts = Facts.EMPTY,
    val branching: BranchingState = BranchingState.EMPTY,
) {
    companion object {
        val DEFAULT = ContextState()
    }
}
