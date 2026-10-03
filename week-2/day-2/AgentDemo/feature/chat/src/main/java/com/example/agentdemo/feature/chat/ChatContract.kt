package com.example.agentdemo.feature.chat

import com.example.agentdemo.domain.model.Message

/** Иммутабельное состояние экрана чата (MVI: State). */
data class ChatState(
    val agentName: String = "",
    val agentRole: String = "",
    val input: String = "",
    val messages: List<Message> = emptyList(),
    val isSending: Boolean = false,
) {
    val canSend: Boolean get() = input.isNotBlank() && !isSending
}

/** Намерения пользователя (MVI: Intent). Единственный способ изменить состояние. */
sealed interface ChatIntent {
    data class InputChanged(val text: String) : ChatIntent
    data object Send : ChatIntent
    data object Clear : ChatIntent
}

/** Одноразовые события для UI (MVI: Effect). */
sealed interface ChatEffect {
    data class ShowError(val message: String) : ChatEffect
}
