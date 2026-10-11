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
import com.example.agentdemo.domain.profile.UserProfile
import com.example.agentdemo.domain.profile.UserProfileRepository
import com.example.agentdemo.domain.task.TaskState
import com.example.agentdemo.domain.task.TaskStateMachine
import com.example.agentdemo.domain.task.TaskStateRepository
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
 *
 * Поверх памяти — слой персонализации ([profiles]): активный профиль
 * пользователя автоматически входит в тот же [MemoryContext] и задаёт стиль
 * ответа. Переключение профиля меняет ответы, не трогая память.
 *
 * Отдельно — формализованное состояние задачи ([taskState]): конечный автомат
 * (этап → шаг → ожидаемое действие). Переходы считает чистый [TaskStateMachine],
 * снимок сохраняется на диск и входит в тот же [MemoryContext], поэтому после
 * паузы или перезапуска агент продолжает с того же шага без повторных объяснений.
 */
class ChatViewModel(
    private val askAgent: AskAgentUseCase,
    private val conversation: ConversationRepository,
    private val longTermMemory: LongTermMemoryRepository,
    private val workingMemory: WorkingMemoryRepository,
    private val profiles: UserProfileRepository,
    private val taskState: TaskStateRepository,
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
            val allProfiles = profiles.loadAll()
            val activeId = profiles.activeId()
            val task = taskState.load()
            _state.update {
                it.copy(
                    messages = if (saved.isNotEmpty()) saved else it.messages,
                    longTerm = longTerm,
                    working = working,
                    profiles = allProfiles,
                    activeProfileId = activeId,
                    task = task,
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

            ChatIntent.OpenProfiles -> _state.update { it.copy(isProfileSheetVisible = true) }
            ChatIntent.CloseProfiles -> _state.update { it.copy(isProfileSheetVisible = false) }
            is ChatIntent.SelectProfile -> selectProfile(intent.id)
            is ChatIntent.SaveProfile -> saveProfile(intent.profile)
            is ChatIntent.DeleteProfile -> deleteProfile(intent.id)

            ChatIntent.OpenTask -> _state.update { it.copy(isTaskSheetVisible = true) }
            ChatIntent.CloseTask -> _state.update { it.copy(isTaskSheetVisible = false) }
            is ChatIntent.StartTask -> startTask(intent.goal)
            ChatIntent.AdvanceStage -> mutateTask { TaskStateMachine.advance(it) }
            ChatIntent.RollbackStage -> mutateTask { TaskStateMachine.rollback(it) }
            is ChatIntent.UpdateStep ->
                mutateTask { TaskStateMachine.updateStep(it, intent.step, intent.expectedAction) }
            ChatIntent.PauseTask -> mutateTask { TaskStateMachine.pause(it) }
            ChatIntent.ResumeTask -> mutateTask { TaskStateMachine.resume(it) }
            ChatIntent.ResetTask -> resetTask()
        }
    }

    private fun send() {
        val current = _state.value
        if (!current.canSend) return

        val query = current.input.trim()
        // Краткосрочная память — сообщения ДО нового запроса.
        val history = current.messages
        // Долговременная + рабочая память + активный профиль → в системный промпт.
        // Профиль подключается к КАЖДОМУ запросу автоматически, без действий пользователя.
        val memory = MemoryContext(
            longTerm = current.longTerm,
            working = current.working,
            profile = current.activeProfile,
            task = current.task,
        )

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

    // --- Профиль (персонализация) ---

    private fun selectProfile(id: String) {
        _state.update { it.copy(activeProfileId = id) }
        viewModelScope.launch { profiles.setActive(id) }
    }

    /** Создаёт новый профиль (пустой id) или перезаписывает существующий. */
    private fun saveProfile(profile: UserProfile) {
        if (profile.name.isBlank()) return
        val isNew = profile.id.isBlank()
        val saved = if (isNew) profile.copy(id = UUID.randomUUID().toString()) else profile
        _state.update { state ->
            val others = state.profiles.filterNot { it.id == saved.id }
            state.copy(
                profiles = others + saved,
                // Новый профиль сразу становится активным, если активного ещё нет.
                activeProfileId = state.activeProfileId ?: saved.id,
            )
        }
        viewModelScope.launch { profiles.save(saved) }
    }

    private fun deleteProfile(id: String) {
        _state.update { state ->
            val remaining = state.profiles.filterNot { it.id == id }
            val active = if (state.activeProfileId == id) remaining.firstOrNull()?.id
            else state.activeProfileId
            state.copy(profiles = remaining, activeProfileId = active)
        }
        viewModelScope.launch { profiles.delete(id) }
    }

    // --- Задача как конечный автомат ---

    /** Стартует новый автомат задачи с этапа «Планирование». */
    private fun startTask(goal: String) {
        val trimmed = goal.trim()
        if (trimmed.isEmpty()) return
        val task = TaskStateMachine.start(trimmed)
        _state.update { it.copy(task = task) }
        viewModelScope.launch { taskState.save(task) }
    }

    /**
     * Применяет переход автомата к текущей задаче и сохраняет результат.
     *
     * Все переходы (advance/rollback/pause/resume/updateStep) идут через чистый
     * [TaskStateMachine], поэтому легальность состояний гарантирована в одном
     * месте. Если задачи нет или состояние не изменилось — ничего не делаем.
     */
    private fun mutateTask(transition: (TaskState) -> TaskState) {
        val current = _state.value.task ?: return
        val next = transition(current)
        if (next == current) return
        _state.update { it.copy(task = next) }
        viewModelScope.launch { taskState.save(next) }
    }

    /** Сбрасывает задачу — стирает состояние автомата. */
    private fun resetTask() {
        _state.update { it.copy(task = null) }
        viewModelScope.launch { taskState.clear() }
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
