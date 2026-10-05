package com.example.agentdemo.feature.chat

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.example.agentdemo.domain.agent.Agent
import com.example.agentdemo.domain.conversation.ConversationRepository
import com.example.agentdemo.domain.memory.LongTermFact
import com.example.agentdemo.domain.memory.LongTermMemoryRepository
import com.example.agentdemo.domain.memory.MemoryContext
import com.example.agentdemo.domain.memory.WorkingMemory
import com.example.agentdemo.domain.memory.WorkingMemoryRepository
import com.example.agentdemo.domain.model.Message
import com.example.agentdemo.domain.model.Role
import com.example.agentdemo.domain.usecase.AskAgentUseCase
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.receiveAsFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import java.util.UUID

/**
 * MVI-ViewModel экрана чата с явной моделью памяти (три слоя).
 *
 * Единая точка входа — [onIntent]. Состояние отдаётся через [state],
 * одноразовые события — через [effects]. Бизнес-логику делегирует use case.
 *
 * Память разделена на три слоя, каждый со своим хранилищем:
 * - краткосрочная — [conversation] (история диалога);
 * - рабочая — [workingMemory] (текущая задача);
 * - долговременная — [longTermMemory] (профиль, решения, знания).
 *
 * Пользователь явно выбирает, что и в какой слой сохранить (интенты памяти), а
 * при отправке запроса долговременная и рабочая память собираются в
 * [MemoryContext] и передаются агенту — так видно, как слои влияют на ответ.
 */
class ChatViewModel(
    private val askAgent: AskAgentUseCase,
    private val conversation: ConversationRepository,
    private val longTermMemory: LongTermMemoryRepository,
    private val workingMemory: WorkingMemoryRepository,
    agent: Agent,
) : ViewModel() {

    private val _state = MutableStateFlow(
        ChatState(agentName = agent.name, agentRole = agent.role),
    )
    val state: StateFlow<ChatState> = _state.asStateFlow()

    private val _effects = Channel<ChatEffect>(Channel.BUFFERED)
    val effects: Flow<ChatEffect> = _effects.receiveAsFlow()

    init {
        // Восстанавливаем все три слоя памяти с диска — агент «продолжает»,
        // будто не выключался, и помнит профиль/задачу из прошлых сессий.
        viewModelScope.launch {
            val saved = conversation.load()
            val longTerm = longTermMemory.load()
            val working = workingMemory.load()
            _state.update {
                it.copy(
                    messages = if (saved.isNotEmpty()) saved else it.messages,
                    longTerm = longTerm,
                    working = working,
                )
            }
        }
    }

    fun onIntent(intent: ChatIntent) {
        when (intent) {
            is ChatIntent.InputChanged -> _state.update { it.copy(input = intent.text) }
            ChatIntent.Send -> send()
            ChatIntent.Clear -> clear()

            ChatIntent.OpenMemory -> _state.update { it.copy(isMemorySheetVisible = true) }
            ChatIntent.CloseMemory -> _state.update { it.copy(isMemorySheetVisible = false) }

            is ChatIntent.AddLongTermFact -> addLongTermFact(intent)
            is ChatIntent.RemoveLongTermFact -> removeLongTermFact(intent.id)
            ChatIntent.ClearLongTerm -> clearLongTerm()

            is ChatIntent.SetTask -> setTask(intent.task)
            is ChatIntent.AddWorkingNote -> addWorkingNote(intent.note)
            ChatIntent.FinishTask -> finishTask()
        }
    }

    private fun send() {
        val current = _state.value
        if (!current.canSend) return

        val query = current.input.trim()
        // Краткосрочная память — сообщения ДО нового запроса.
        val history = current.messages
        // Долговременная + рабочая память → в системный промпт запроса.
        val memory = MemoryContext(longTerm = current.longTerm, working = current.working)

        val afterUser = _state.updateAndGetMessages {
            it.copy(
                input = "",
                isSending = true,
                messages = it.messages + Message(Role.USER, query),
            )
        }

        viewModelScope.launch {
            // Сохраняем сразу с вопросом пользователя: если приложение убьют во
            // время запроса, реплика не потеряется.
            conversation.save(afterUser)

            askAgent(query, history, memory)
                .onSuccess { reply ->
                    val afterReply = _state.updateAndGetMessages {
                        it.copy(
                            isSending = false,
                            messages = it.messages + Message(Role.AGENT, reply.text),
                        )
                    }
                    conversation.save(afterReply)
                }
                .onFailure { error ->
                    _state.update { it.copy(isSending = false) }
                    _effects.send(ChatEffect.ShowError(error.message ?: "Неизвестная ошибка"))
                }
        }
    }

    /** Очистка только краткосрочной памяти — диалога. Остальные слои не трогаем. */
    private fun clear() {
        _state.update { it.copy(messages = emptyList()) }
        viewModelScope.launch { conversation.clear() }
    }

    // --- Долговременная память ---

    private fun addLongTermFact(intent: ChatIntent.AddLongTermFact) {
        val text = intent.text.trim()
        if (text.isEmpty()) return
        val fact = LongTermFact(id = UUID.randomUUID().toString(), kind = intent.kind, text = text)
        _state.update { it.copy(longTerm = it.longTerm + fact) }
        viewModelScope.launch { longTermMemory.add(fact) }
    }

    private fun removeLongTermFact(id: String) {
        _state.update { it.copy(longTerm = it.longTerm.filterNot { fact -> fact.id == id }) }
        viewModelScope.launch { longTermMemory.remove(id) }
    }

    private fun clearLongTerm() {
        _state.update { it.copy(longTerm = emptyList()) }
        viewModelScope.launch { longTermMemory.clear() }
    }

    // --- Рабочая память ---

    private fun setTask(task: String) {
        val trimmed = task.trim()
        if (trimmed.isEmpty()) return
        // Смена задачи начинает новую рабочую память (заметки прежней задачи сбрасываются).
        val working = WorkingMemory(task = trimmed, notes = emptyList())
        _state.update { it.copy(working = working) }
        viewModelScope.launch { workingMemory.save(working) }
    }

    private fun addWorkingNote(note: String) {
        val trimmed = note.trim()
        val current = _state.value.working ?: return // заметка без задачи бессмысленна
        if (trimmed.isEmpty()) return
        val working = current.copy(notes = current.notes + trimmed)
        _state.update { it.copy(working = working) }
        viewModelScope.launch { workingMemory.save(working) }
    }

    /** Задача завершена — рабочая память стирается (в отличие от долговременной). */
    private fun finishTask() {
        _state.update { it.copy(working = null) }
        viewModelScope.launch { workingMemory.clear() }
    }

    /** Атомарно обновляет состояние и возвращает получившийся список сообщений. */
    private inline fun MutableStateFlow<ChatState>.updateAndGetMessages(
        transform: (ChatState) -> ChatState,
    ): List<Message> {
        var result: List<Message> = emptyList()
        update { current -> transform(current).also { result = it.messages } }
        return result
    }
}
