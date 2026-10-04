package com.example.agentdemo.feature.chat

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.example.agentdemo.domain.agent.Agent
import com.example.agentdemo.domain.context.AgentMemoryRepository
import com.example.agentdemo.domain.context.ConversationMemory
import com.example.agentdemo.domain.conversation.ConversationRepository
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

/**
 * MVI-ViewModel экрана чата с управлением контекстом.
 *
 * Единая точка входа — [onIntent]. Состояние отдаётся через [state],
 * одноразовые события — через [effects]. Бизнес-логику делегирует use case.
 *
 * Контекст беседы переживает перезапуск: при создании ViewModel из хранилищ
 * подгружаются и лента ([conversation]), и состояние сжатия ([agentMemory]) —
 * summary хранится отдельно от истории. На каждой отправке выбранный режим
 * (сжатие вкл/выкл) определяет, что именно уйдёт в модель, а [ChatState.lastSend]
 * показывает это для сравнения.
 */
class ChatViewModel(
    private val askAgent: AskAgentUseCase,
    private val conversation: ConversationRepository,
    private val agentMemory: AgentMemoryRepository,
    agent: Agent,
) : ViewModel() {

    private val _state = MutableStateFlow(
        ChatState(agentName = agent.name, agentRole = agent.role),
    )
    val state: StateFlow<ChatState> = _state.asStateFlow()

    private val _effects = Channel<ChatEffect>(Channel.BUFFERED)
    val effects: Flow<ChatEffect> = _effects.receiveAsFlow()

    /** Состояние сжатия (summary + граница свёртки). Владеет им ViewModel, персистит [agentMemory]. */
    private var memory: ConversationMemory = ConversationMemory()

    init {
        // Восстанавливаем диалог и summary с диска — агент «продолжает», будто не выключался.
        viewModelScope.launch {
            val saved = conversation.load()
            memory = agentMemory.load()
            _state.update {
                it.copy(
                    messages = if (saved.isNotEmpty()) saved else it.messages,
                    summary = memory.summary,
                )
            }
        }
    }

    fun onIntent(intent: ChatIntent) {
        when (intent) {
            is ChatIntent.InputChanged -> _state.update { it.copy(input = intent.text) }
            ChatIntent.Send -> send()
            ChatIntent.Clear -> clear()
            ChatIntent.ToggleCompression ->
                _state.update { it.copy(compressionEnabled = !it.compressionEnabled) }
            ChatIntent.ShowSummary -> _state.update { it.copy(showSummary = true) }
            ChatIntent.DismissSummary -> _state.update { it.copy(showSummary = false) }
        }
    }

    private fun send() {
        val current = _state.value
        if (!current.canSend) return

        val query = current.input.trim()
        // История — сообщения ДО нового запроса; передаём её агенту для контекста.
        val history = current.messages
        val compress = current.compressionEnabled
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

            askAgent(query, history, memory, compress)
                .onSuccess { outcome ->
                    // Обновлённое состояние сжатия сохраняем отдельно от ленты.
                    memory = outcome.memory
                    agentMemory.save(memory)

                    val afterReply = _state.updateAndGetMessages {
                        it.copy(
                            isSending = false,
                            messages = it.messages + Message(Role.AGENT, outcome.reply.text),
                            summary = memory.summary,
                            lastSend = outcome.stats,
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

    private fun clear() {
        memory = ConversationMemory()
        _state.update {
            it.copy(messages = emptyList(), summary = "", lastSend = null, showSummary = false)
        }
        viewModelScope.launch {
            conversation.clear()
            agentMemory.clear()
        }
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
