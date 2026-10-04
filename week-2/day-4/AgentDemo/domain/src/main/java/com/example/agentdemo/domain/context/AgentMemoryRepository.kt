package com.example.agentdemo.domain.context

/**
 * Порт персистентности состояния сжатия ([ConversationMemory]).
 *
 * Summary хранится ОТДЕЛЬНО от ленты диалога (прямое требование задания) — в
 * своём файле/хранилище. Это позволяет подставлять его в запрос вместо полной
 * истории и переживать перезапуск приложения. По аналогии с
 * [com.example.agentdemo.domain.conversation.ConversationRepository].
 */
interface AgentMemoryRepository {

    /** Загрузить сохранённое состояние сжатия (пустое, если его ещё нет). */
    suspend fun load(): ConversationMemory

    /** Перезаписать сохранённое состояние сжатия. */
    suspend fun save(memory: ConversationMemory)

    /** Стереть сохранённое состояние сжатия. */
    suspend fun clear()
}
