package com.example.agentdemo.domain.invariant

/**
 * Порт хранилища инвариантов.
 *
 * Отдельное хранилище от диалога и остальной памяти — требование задания:
 * инварианты живут независимо от разговора и переживают его очистку. Реализацию
 * (JSON-файл) поставляет слой data; она же засеивает набор инвариантов проекта
 * по умолчанию при первом запуске.
 */
interface InvariantRepository {

    /** Все сохранённые инварианты (и активные, и временно снятые). */
    suspend fun loadAll(): List<Invariant>

    /** Создать новый или перезаписать существующий инвариант (по [Invariant.id]). */
    suspend fun save(invariant: Invariant)

    /** Удалить инвариант по id. */
    suspend fun delete(id: String)

    /** Переключить активность инварианта (учитывать / временно снять). */
    suspend fun setActive(id: String, isActive: Boolean)

    /** Удалить все инварианты. */
    suspend fun clear()
}
