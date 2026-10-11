package com.example.agentdemo.domain.memory

import com.example.agentdemo.domain.profile.UserProfile
import com.example.agentdemo.domain.task.TaskState

/**
 * Снимок «постоянной» памяти (долговременная + рабочая) плюс активный профиль
 * пользователя и состояние текущей задачи, который передаётся агенту вместе с
 * запросом и историей диалога.
 *
 * Краткосрочный слой (история диалога) сюда не входит — он передаётся агенту
 * отдельным параметром `history`, как и раньше. Так в одном месте видно, какие
 * именно слои влияют на системный промпт, а какой — на список сообщений.
 *
 * [profile] — слой персонализации поверх памяти: не факты, а предпочтения по
 * стилю/формату ответа. [task] — формализованное состояние задачи (конечный
 * автомат): этап, текущий шаг, ожидаемое действие и журнал. Оба, как и память,
 * уходят в системный промпт автоматически, поэтому агент продолжает работу с
 * того же шага без повторных объяснений.
 */
data class MemoryContext(
    val longTerm: List<LongTermFact> = emptyList(),
    val working: WorkingMemory? = null,
    val profile: UserProfile? = null,
    val task: TaskState? = null,
) {
    /** Есть ли что встраивать в системный промпт. */
    val isEmpty: Boolean
        get() = longTerm.isEmpty() && working == null && profile == null && task == null

    companion object {
        val EMPTY = MemoryContext()
    }
}
