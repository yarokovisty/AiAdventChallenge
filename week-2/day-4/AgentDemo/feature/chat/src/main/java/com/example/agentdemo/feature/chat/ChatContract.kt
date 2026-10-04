package com.example.agentdemo.feature.chat

import com.example.agentdemo.domain.context.ContextStats
import com.example.agentdemo.domain.model.Message

/** Иммутабельное состояние экрана чата (MVI: State). */
data class ChatState(
    val agentName: String = "",
    val agentRole: String = "",
    val input: String = "",
    val messages: List<Message> = emptyList(),
    val isSending: Boolean = false,
    /** Режим управления контекстом: сворачивать старое в summary или слать всё как есть. */
    val compressionEnabled: Boolean = true,
    /** Текущий summary свёрнутой части диалога (для просмотра пользователем). */
    val summary: String = "",
    /** Что ушло в модель на последней отправке — для сравнения режимов. */
    val lastSend: ContextStats? = null,
    /** Показывать ли диалог с текстом summary. */
    val showSummary: Boolean = false,
) {
    val canSend: Boolean get() = input.isNotBlank() && !isSending
}

/** Намерения пользователя (MVI: Intent). Единственный способ изменить состояние. */
sealed interface ChatIntent {
    data class InputChanged(val text: String) : ChatIntent
    data object Send : ChatIntent
    data object Clear : ChatIntent
    /** Переключить режим сжатия контекста (для сравнения «со сжатием» / «без»). */
    data object ToggleCompression : ChatIntent
    /** Открыть просмотр текущего summary. */
    data object ShowSummary : ChatIntent
    /** Закрыть просмотр summary. */
    data object DismissSummary : ChatIntent
}

/** Одноразовые события для UI (MVI: Effect). */
sealed interface ChatEffect {
    data class ShowError(val message: String) : ChatEffect
}
