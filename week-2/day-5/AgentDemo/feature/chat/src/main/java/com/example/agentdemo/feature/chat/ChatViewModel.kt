package com.example.agentdemo.feature.chat

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.example.agentdemo.domain.agent.Agent
import com.example.agentdemo.domain.context.Branch
import com.example.agentdemo.domain.context.BranchingState
import com.example.agentdemo.domain.context.ContextManager
import com.example.agentdemo.domain.context.ContextState
import com.example.agentdemo.domain.context.ContextStateRepository
import com.example.agentdemo.domain.context.ContextStrategy
import com.example.agentdemo.domain.context.Facts
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
import kotlinx.coroutines.flow.updateAndGet
import kotlinx.coroutines.launch
import java.util.UUID

/**
 * MVI-ViewModel экрана чата с переключаемым управлением контекстом.
 *
 * Держит выбранную [ContextStrategy] и её данные (факты, ветки), прокидывает их
 * в [askAgent] и показывает метрику последней отправки. Всё состояние контекста
 * переживает перезапуск: история — в [conversation], стратегия/факты/ветки —
 * в [contextStore].
 *
 * Про ветки: активная ветка живёт прямо в [ChatState.messages] (и, как и раньше,
 * пишется в историю диалога), а снапшоты всех веток — в [ContextState.branching].
 * Поэтому при переключении ветки мы сперва «сливаем» текущие сообщения в снапшот
 * активной ветки, затем подставляем сообщения выбранной.
 */
class ChatViewModel(
    private val askAgent: AskAgentUseCase,
    private val conversation: ConversationRepository,
    private val contextStore: ContextStateRepository,
    contextManager: ContextManager,
    agent: Agent,
) : ViewModel() {

    private val _state = MutableStateFlow(
        ChatState(
            agentName = agent.name,
            agentRole = agent.role,
            windowSize = contextManager.windowSize,
        ),
    )
    val state: StateFlow<ChatState> = _state.asStateFlow()

    private val _effects = Channel<ChatEffect>(Channel.BUFFERED)
    val effects: Flow<ChatEffect> = _effects.receiveAsFlow()

    init {
        // Восстанавливаем и диалог, и состояние контекста — агент «продолжает»,
        // будто не выключался, с той же стратегией и фактами.
        viewModelScope.launch {
            val saved = conversation.load()
            val ctx = contextStore.load()
            _state.update {
                it.copy(
                    messages = saved,
                    strategy = ctx.strategy,
                    facts = ctx.facts,
                    branching = ctx.branching,
                )
            }
        }
    }

    fun onIntent(intent: ChatIntent) {
        when (intent) {
            is ChatIntent.InputChanged -> _state.update { it.copy(input = intent.text) }
            ChatIntent.Send -> send()
            ChatIntent.Clear -> clear()
            is ChatIntent.SelectStrategy -> selectStrategy(intent.strategy)
            ChatIntent.ToggleFacts -> _state.update { it.copy(showFacts = !it.showFacts) }
            ChatIntent.Checkpoint -> checkpoint()
            ChatIntent.Fork -> fork()
            is ChatIntent.SwitchBranch -> switchBranch(intent.branchId)
        }
    }

    private fun send() {
        val current = _state.value
        if (!current.canSend) return

        val query = current.input.trim()
        val strategy = current.strategy
        val facts = current.facts
        // История — сообщения ДО нового запроса; передаём её агенту для контекста.
        val history = current.messages
        val afterUser = _state.updateAndGet {
            it.copy(
                input = "",
                isSending = true,
                messages = it.messages + Message(Role.USER, query),
            )
        }

        viewModelScope.launch {
            // Сохраняем сразу с вопросом пользователя: если приложение убьют во
            // время запроса, реплика не потеряется.
            persist(afterUser)

            askAgent(query = query, strategy = strategy, history = history, facts = facts)
                .onSuccess { outcome ->
                    val afterReply = _state.updateAndGet {
                        it.copy(
                            isSending = false,
                            messages = it.messages + Message(Role.AGENT, outcome.reply.text),
                            facts = outcome.facts,
                            lastStats = outcome.stats,
                        )
                    }
                    persist(afterReply)
                }
                .onFailure { error ->
                    _state.update { it.copy(isSending = false) }
                    _effects.send(ChatEffect.ShowError(error.message ?: "Неизвестная ошибка"))
                }
        }
    }

    private fun selectStrategy(strategy: ContextStrategy) {
        val newState = _state.updateAndGet { it.copy(strategy = strategy) }
        persist(newState)
    }

    private fun checkpoint() {
        val newState = _state.updateAndGet {
            it.copy(branching = it.branching.copy(checkpointIndex = it.messages.size))
        }
        persist(newState)
        emit(ChatEffect.ShowMessage("Чекпоинт поставлен на ${newState.messages.size} сообщ."))
    }

    private fun fork() {
        val current = _state.value
        if (current.branching.isForked) {
            emit(ChatEffect.ShowMessage("Ветки уже созданы — переключайтесь между ними"))
            return
        }
        val checkpoint = current.branching.checkpointIndex ?: current.messages.size
        val base = current.messages.take(checkpoint)
        val branchA = Branch(id = newId(), name = "Ветка A", messages = base)
        val branchB = Branch(id = newId(), name = "Ветка B", messages = base)
        val newState = current.copy(
            messages = base,
            branching = BranchingState(
                branches = listOf(branchA, branchB),
                activeId = branchA.id,
                checkpointIndex = checkpoint,
            ),
        )
        _state.value = newState
        persist(newState)
        emit(ChatEffect.ShowMessage("Созданы 2 ветки от сообщения №$checkpoint"))
    }

    private fun switchBranch(branchId: String) {
        val current = _state.value
        // Сначала фиксируем текущие сообщения в снапшоте активной ветки.
        val synced = current.syncedBranching()
        val target = synced.branches.firstOrNull { it.id == branchId } ?: return
        val newState = current.copy(
            messages = target.messages,
            branching = synced.copy(activeId = branchId),
        )
        _state.value = newState
        persist(newState)
        emit(ChatEffect.ShowMessage("Переключено на «${target.name}»"))
    }

    private fun clear() {
        val strategy = _state.value.strategy
        _state.update {
            it.copy(
                messages = emptyList(),
                facts = Facts.EMPTY,
                branching = BranchingState.EMPTY,
                lastStats = null,
            )
        }
        viewModelScope.launch {
            conversation.clear()
            // Сохраняем выбор стратегии, но сбрасываем её данные.
            contextStore.save(ContextState(strategy = strategy))
        }
    }

    /** Сохраняет историю и состояние контекста (с синхронизацией активной ветки). */
    private fun persist(state: ChatState) {
        viewModelScope.launch {
            conversation.save(state.messages)
            contextStore.save(
                ContextState(
                    strategy = state.strategy,
                    facts = state.facts,
                    branching = state.syncedBranching(),
                ),
            )
        }
    }

    /** Возвращает состояние веток с актуальными сообщениями активной ветки. */
    private fun ChatState.syncedBranching(): BranchingState {
        val b = branching
        if (!b.isForked) return b
        return b.copy(
            branches = b.branches.map { if (it.id == b.activeId) it.copy(messages = messages) else it },
        )
    }

    private fun emit(effect: ChatEffect) {
        viewModelScope.launch { _effects.send(effect) }
    }

    private fun newId(): String = UUID.randomUUID().toString()
}
