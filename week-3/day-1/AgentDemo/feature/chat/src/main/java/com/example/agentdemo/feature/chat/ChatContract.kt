package com.example.agentdemo.feature.chat

import com.example.agentdemo.domain.memory.KnowledgeKind
import com.example.agentdemo.domain.memory.LongTermFact
import com.example.agentdemo.domain.memory.WorkingMemory
import com.example.agentdemo.domain.model.Message

/** Иммутабельное состояние экрана чата (MVI: State). */
data class ChatState(
    val agentName: String = "",
    val agentRole: String = "",
    val input: String = "",
    val messages: List<Message> = emptyList(),
    val isSending: Boolean = false,
    // Три слоя памяти в состоянии экрана. Краткосрочная — это [messages].
    val longTerm: List<LongTermFact> = emptyList(),
    val working: WorkingMemory? = null,
    val isMemorySheetVisible: Boolean = false,
) {
    val canSend: Boolean get() = input.isNotBlank() && !isSending

    /** Сколько единиц информации лежит в долговременной и рабочей памяти. */
    val persistentMemoryCount: Int
        get() = longTerm.size + (working?.let { 1 + it.notes.size } ?: 0)
}

/** Намерения пользователя (MVI: Intent). Единственный способ изменить состояние. */
sealed interface ChatIntent {
    data class InputChanged(val text: String) : ChatIntent
    data object Send : ChatIntent
    data object Clear : ChatIntent

    // --- Память: явный выбор, что и в какой слой сохранить ---
    data object OpenMemory : ChatIntent
    data object CloseMemory : ChatIntent

    /** Долговременная память: добавить факт выбранного вида / удалить / очистить. */
    data class AddLongTermFact(val kind: KnowledgeKind, val text: String) : ChatIntent
    data class RemoveLongTermFact(val id: String) : ChatIntent
    data object ClearLongTerm : ChatIntent

    /** Рабочая память: задать текущую задачу / добавить заметку / завершить задачу. */
    data class SetTask(val task: String) : ChatIntent
    data class AddWorkingNote(val note: String) : ChatIntent
    data object FinishTask : ChatIntent
}

/** Одноразовые события для UI (MVI: Effect). */
sealed interface ChatEffect {
    data class ShowError(val message: String) : ChatEffect
}
