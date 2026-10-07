package com.example.agentdemo.domain.memory

import com.example.agentdemo.domain.profile.UserProfile

/**
 * Снимок «постоянной» памяти (долговременная + рабочая) плюс активный профиль
 * пользователя, который передаётся агенту вместе с запросом и историей диалога.
 *
 * Краткосрочный слой (история диалога) сюда не входит — он передаётся агенту
 * отдельным параметром `history`, как и раньше. Так в одном месте видно, какие
 * именно слои влияют на системный промпт, а какой — на список сообщений.
 *
 * [profile] — слой персонализации поверх памяти: не факты, а предпочтения по
 * стилю/формату ответа. Как и память, уходит в системный промпт автоматически.
 */
data class MemoryContext(
    val longTerm: List<LongTermFact> = emptyList(),
    val working: WorkingMemory? = null,
    val profile: UserProfile? = null,
) {
    /** Есть ли что встраивать в системный промпт. */
    val isEmpty: Boolean get() = longTerm.isEmpty() && working == null && profile == null

    companion object {
        val EMPTY = MemoryContext()
    }
}
