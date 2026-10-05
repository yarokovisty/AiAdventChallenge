package com.example.agentdemo.domain.memory

/**
 * Снимок «постоянной» памяти (долговременная + рабочая), который передаётся
 * агенту вместе с запросом и историей диалога.
 *
 * Краткосрочный слой (история диалога) сюда не входит — он передаётся агенту
 * отдельным параметром `history`, как и раньше. Так в одном месте видно, какие
 * именно слои влияют на системный промпт, а какой — на список сообщений.
 */
data class MemoryContext(
    val longTerm: List<LongTermFact> = emptyList(),
    val working: WorkingMemory? = null,
) {
    /** Есть ли что встраивать в системный промпт. */
    val isEmpty: Boolean get() = longTerm.isEmpty() && working == null

    companion object {
        val EMPTY = MemoryContext()
    }
}
