package com.example.agentdemo.feature.chat

import com.example.agentdemo.domain.invariant.Invariant
import com.example.agentdemo.domain.invariant.InvariantCategory
import com.example.agentdemo.domain.memory.KnowledgeKind
import com.example.agentdemo.domain.memory.LongTermFact
import com.example.agentdemo.domain.memory.WorkingMemory
import com.example.agentdemo.domain.model.Message
import com.example.agentdemo.domain.profile.UserProfile
import com.example.agentdemo.domain.task.TaskStage
import com.example.agentdemo.domain.task.TaskState

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
    // Персонализация поверх памяти: профили и id активного.
    val profiles: List<UserProfile> = emptyList(),
    val activeProfileId: String? = null,
    val isProfileSheetVisible: Boolean = false,
    // Формализованное состояние задачи (конечный автомат); null — активной задачи нет.
    val task: TaskState? = null,
    val isTaskSheetVisible: Boolean = false,
    // Причина последнего отклонённого перехода (перепрыгивание этапа / закрытый
    // шлюз) — показывается в панели задачи как видимая «реакция» автомата.
    val lastTaskRejection: String? = null,
    // Инварианты: жёсткие правила, которые агент не вправе нарушить.
    val invariants: List<Invariant> = emptyList(),
    val isInvariantSheetVisible: Boolean = false,
) {
    val canSend: Boolean get() = input.isNotBlank() && !isSending

    /** Сколько инвариантов сейчас активно (именно они уходят в запрос). */
    val activeInvariantCount: Int get() = invariants.count { it.isActive }

    /** Сколько единиц информации лежит в долговременной и рабочей памяти. */
    val persistentMemoryCount: Int
        get() = longTerm.size + (working?.let { 1 + it.notes.size } ?: 0)

    /** Активный профиль — именно он уходит в каждый запрос. */
    val activeProfile: UserProfile?
        get() = profiles.firstOrNull { it.id == activeProfileId }
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

    // --- Профиль (персонализация): выбор активного и редактирование ---
    data object OpenProfiles : ChatIntent
    data object CloseProfiles : ChatIntent

    /** Сделать профиль активным — он уходит в каждый запрос. */
    data class SelectProfile(val id: String) : ChatIntent

    /** Создать новый или перезаписать существующий профиль. */
    data class SaveProfile(val profile: UserProfile) : ChatIntent
    data class DeleteProfile(val id: String) : ChatIntent

    // --- Задача как конечный автомат: этап → шаг → ожидаемое действие ---
    data object OpenTask : ChatIntent
    data object CloseTask : ChatIntent

    /** Начать новую задачу с цели — автомат стартует с этапа «Планирование». */
    data class StartTask(val goal: String) : ChatIntent

    /** Перейти на следующий этап (planning→execution→validation→done). */
    data object AdvanceStage : ChatIntent

    /** Вернуть задачу с проверки на доработку (validation → execution). */
    data object RollbackStage : ChatIntent

    /**
     * Попытка перейти сразу в выбранный этап (в т.ч. с перепрыгиванием) — для
     * демонстрации отказа автомата на недопустимом переходе.
     */
    data class RequestStage(val target: TaskStage) : ChatIntent

    /** Утвердить план — открыть шлюз перехода к реализации. */
    data object ApprovePlan : ChatIntent

    /** Отметить валидацию пройденной — открыть шлюз завершения. */
    data object MarkValidationPassed : ChatIntent

    /** Уточнить текущий шаг и ожидаемое действие, не меняя этап. */
    data class UpdateStep(val step: String, val expectedAction: String) : ChatIntent

    /** Пауза на текущем этапе / продолжение с того же шага. */
    data object PauseTask : ChatIntent
    data object ResumeTask : ChatIntent

    /** Сбросить задачу (стереть состояние автомата). */
    data object ResetTask : ChatIntent

    // --- Инварианты: жёсткие правила, которые агент не вправе нарушить ---
    data object OpenInvariants : ChatIntent
    data object CloseInvariants : ChatIntent

    /** Добавить новый инвариант выбранной категории. */
    data class AddInvariant(
        val category: InvariantCategory,
        val rule: String,
        val rationale: String,
    ) : ChatIntent

    /** Временно снять инвариант / вернуть его в силу (не удаляя). */
    data class ToggleInvariant(val id: String, val isActive: Boolean) : ChatIntent

    /** Удалить инвариант. */
    data class RemoveInvariant(val id: String) : ChatIntent
}

/** Одноразовые события для UI (MVI: Effect). */
sealed interface ChatEffect {
    data class ShowError(val message: String) : ChatEffect
}
