package com.example.agentdemo.feature.chat

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.example.agentdemo.domain.agent.Agent
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
 * MVI-ViewModel экрана чата.
 *
 * Единая точка входа — [onIntent]. Состояние отдаётся через [state],
 * одноразовые события — через [effects]. Бизнес-логику делегирует use case,
 * сам о сети и об устройстве агента ничего не знает.
 *
 * Контекст беседы переживает перезапуск: при создании ViewModel история
 * подгружается из [conversation], а каждое изменение ленты тут же сохраняется.
 */
class ChatViewModel(
    private val askAgent: AskAgentUseCase,
    private val conversation: ConversationRepository,
    agent: Agent,
) : ViewModel() {

    private val _state = MutableStateFlow(
        ChatState(agentName = agent.name, agentRole = agent.role),
    )
    val state: StateFlow<ChatState> = _state.asStateFlow()

    private val _effects = Channel<ChatEffect>(Channel.BUFFERED)
    val effects: Flow<ChatEffect> = _effects.receiveAsFlow()

    init {
        // Восстанавливаем диалог с диска — агент «продолжает», будто не выключался.
        viewModelScope.launch {
            val saved = conversation.load()
            if (saved.isNotEmpty()) {
                _state.update { it.copy(messages = saved) }
            }
        }
    }

    fun onIntent(intent: ChatIntent) {
        when (intent) {
            is ChatIntent.InputChanged -> _state.update { it.copy(input = intent.text) }
            ChatIntent.Send -> send()
            ChatIntent.Clear -> clear()
        }
    }

    private fun send() {
        val current = _state.value
        if (!current.canSend) return

        val query = current.input.trim()
        // История — сообщения ДО нового запроса; передаём её агенту для контекста.
        val history = current.messages
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

            askAgent(query, history)
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

    private fun clear() {
        _state.update { it.copy(messages = emptyList()) }
        viewModelScope.launch { conversation.clear() }
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
