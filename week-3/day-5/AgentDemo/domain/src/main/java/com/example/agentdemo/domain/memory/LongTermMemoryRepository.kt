package com.example.agentdemo.domain.memory

/**
 * Порт хранилища долговременной памяти ([MemoryLayer.LONG_TERM]).
 *
 * Отдельный от диалога и от рабочей памяти контракт — слои хранятся раздельно
 * (принцип инверсии зависимостей, как и у остальных портов домена). Реализацию
 * (JSON-файл) поставляет слой data.
 */
interface LongTermMemoryRepository {

    /** Загрузить все факты (пустой список, если памяти ещё нет). */
    suspend fun load(): List<LongTermFact>

    /** Добавить факт. Сохраняется явно по действию пользователя. */
    suspend fun add(fact: LongTermFact)

    /** Удалить факт по идентификатору. */
    suspend fun remove(id: String)

    /** Полностью очистить долговременную память. */
    suspend fun clear()
}
