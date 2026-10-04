package com.example.agentdemo.feature.chat

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.example.agentdemo.domain.agent.Agent
import com.example.agentdemo.domain.conversation.ConversationRepository
import com.example.agentdemo.domain.model.Message
import com.example.agentdemo.domain.model.Role
import com.example.agentdemo.domain.token.AgentLimits
import com.example.agentdemo.domain.token.TokenEstimator
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
 * MVI-ViewModel экрана чата с учётом токенов.
 *
 * Поверх базового агента ведёт счёт токенов:
 * - **оценка** (локально, через [tokenEstimator] и [Agent.estimateRequestTokens]) —
 *   чтобы показывать рост контекста прямо при наборе и предсказывать переполнение
 *   *до* отправки;
 * - **факт** (`usage` из ответа) — чтобы показывать точный расход и считать стоимость.
 *
 * Контекст беседы по-прежнему переживает перезапуск через [conversation].
 */
class ChatViewModel(
    private val askAgent: AskAgentUseCase,
    private val conversation: ConversationRepository,
    private val tokenEstimator: TokenEstimator,
    private val agent: Agent,
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
                _state.update { st -> st.copy(messages = saved.map { it.toUi() }) }
            }
            recomputeEstimates()
        }
    }

    fun onIntent(intent: ChatIntent) {
        when (intent) {
            is ChatIntent.InputChanged -> {
                _state.update { it.copy(input = intent.text) }
                recomputeEstimates()
            }
            ChatIntent.Send -> send()
            ChatIntent.Clear -> clear()
            ChatIntent.ToggleDemoLimit -> _state.update { it.copy(demoLimit = !it.demoLimit) }
        }
    }

    private fun send() {
        val current = _state.value
        if (current.input.isBlank() || current.isSending) return

        // Переполнение — это и есть «что ломается»: агент отказывается продолжать,
        // пока историю не обрежут. Отправку в сеть не делаем.
        if (current.willOverflow) {
            viewModelScope.launch {
                _effects.send(
                    ChatEffect.ShowError(
                        "Контекст переполнен: запрос ~${current.nextRequestTokens} токенов " +
                            "превышает лимит ${current.limit}. Агент не может продолжать — " +
                            "очистите диалог или удалите старые сообщения.",
                    ),
                )
            }
            return
        }

        val query = current.input.trim()
        val history = current.messages.map { it.message }
        // Оценка prompt, который реально уйдёт сейчас, — сохраняем для сверки с usage.
        val estimatedPrompt = agent.estimateRequestTokens(query, history)

        val afterUser = current.messages +
            UiMessage(Message(Role.USER, query), tokenEstimator.estimate(query), exact = false)
        _state.update {
            it.copy(
                input = "",
                isSending = true,
                messages = afterUser,
            )
        }
        recomputeEstimates()

        viewModelScope.launch {
            // Сохраняем сразу с вопросом пользователя — переживёт убийство во время запроса.
            conversation.save(afterUser.map { it.message })

            askAgent(query, history)
                .onSuccess { reply ->
                    val agentTokens = reply.usage?.completionTokens
                        ?: tokenEstimator.estimate(reply.text)
                    val agentMsg = UiMessage(
                        message = Message(Role.AGENT, reply.text),
                        tokens = agentTokens,
                        exact = reply.usage != null,
                    )
                    val newMessages = _state.value.messages + agentMsg
                    _state.update { st ->
                        st.copy(
                            isSending = false,
                            messages = newMessages,
                            lastUsage = reply.usage,
                            // Обновляем оценку prompt вместе с фактом — чтобы строка сверки
                            // всегда сравнивала оценку и usage ОДНОГО и того же запроса.
                            lastEstimatedPrompt = estimatedPrompt,
                            cumulativeTokens = st.cumulativeTokens + (reply.usage?.totalTokens ?: 0),
                            cumulativeCostUsd = st.cumulativeCostUsd +
                                (reply.usage?.let { AgentLimits.costUsd(it) } ?: 0.0),
                        )
                    }
                    recomputeEstimates()
                    conversation.save(newMessages.map { it.message })
                }
                .onFailure { error ->
                    _state.update { it.copy(isSending = false) }
                    _effects.send(ChatEffect.ShowError(error.message ?: "Неизвестная ошибка"))
                }
        }
    }

    private fun clear() {
        _state.update {
            it.copy(
                messages = emptyList(),
                lastUsage = null,
                lastEstimatedPrompt = 0,
                cumulativeTokens = 0,
                cumulativeCostUsd = 0.0,
            )
        }
        recomputeEstimates()
        viewModelScope.launch { conversation.clear() }
    }

    /** Пересчитывает оценки контекста и следующего запроса под текущие историю и ввод. */
    private fun recomputeEstimates() {
        _state.update { st ->
            val history = st.messages.map { it.message }
            st.copy(
                contextTokens = agent.estimateRequestTokens(query = "", history = history),
                nextRequestTokens = agent.estimateRequestTokens(query = st.input, history = history),
            )
        }
    }

    /** Восстановленное с диска сообщение → UI-модель (usage не хранится, поэтому оценка). */
    private fun Message.toUi(): UiMessage =
        UiMessage(message = this, tokens = tokenEstimator.estimate(text), exact = false)
}
