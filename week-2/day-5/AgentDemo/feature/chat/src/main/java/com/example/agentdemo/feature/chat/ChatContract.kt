package com.example.agentdemo.feature.chat

import com.example.agentdemo.domain.context.BranchingState
import com.example.agentdemo.domain.context.ContextStats
import com.example.agentdemo.domain.context.ContextStrategy
import com.example.agentdemo.domain.context.Facts
import com.example.agentdemo.domain.model.Message

/** Иммутабельное состояние экрана чата (MVI: State). */
data class ChatState(
    val agentName: String = "",
    val agentRole: String = "",
    val input: String = "",
    val messages: List<Message> = emptyList(),
    val isSending: Boolean = false,
    // --- управление контекстом ---
    val strategy: ContextStrategy = ContextStrategy.SLIDING_WINDOW,
    val windowSize: Int = 6,
    val facts: Facts = Facts.EMPTY,
    val showFacts: Boolean = false,
    val branching: BranchingState = BranchingState.EMPTY,
    val lastStats: ContextStats? = null,
) {
    val canSend: Boolean get() = input.isNotBlank() && !isSending
    val isBranching: Boolean get() = strategy == ContextStrategy.BRANCHING
}

/** Намерения пользователя (MVI: Intent). Единственный способ изменить состояние. */
sealed interface ChatIntent {
    data class InputChanged(val text: String) : ChatIntent
    data object Send : ChatIntent
    data object Clear : ChatIntent

    /** Переключить активную стратегию управления контекстом. */
    data class SelectStrategy(val strategy: ContextStrategy) : ChatIntent

    /** Показать/скрыть панель фактов. */
    data object ToggleFacts : ChatIntent

    /** Поставить чекпоинт на текущей точке диалога (стратегия Branching). */
    data object Checkpoint : ChatIntent

    /** Создать две ветки от чекпоинта (стратегия Branching). */
    data object Fork : ChatIntent

    /** Переключиться на ветку [branchId]. */
    data class SwitchBranch(val branchId: String) : ChatIntent
}

/** Одноразовые события для UI (MVI: Effect). */
sealed interface ChatEffect {
    data class ShowError(val message: String) : ChatEffect
    data class ShowMessage(val message: String) : ChatEffect
}
