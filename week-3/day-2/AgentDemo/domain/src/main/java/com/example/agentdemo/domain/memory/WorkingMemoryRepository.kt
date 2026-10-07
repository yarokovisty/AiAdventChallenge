package com.example.agentdemo.domain.memory

/**
 * Порт хранилища рабочей памяти ([MemoryLayer.WORKING]).
 *
 * Хранит ровно одну текущую задачу (или ничего). Отдельное хранилище от диалога
 * и долговременной памяти; реализацию (JSON-файл) поставляет слой data.
 */
interface WorkingMemoryRepository {

    /** Загрузить текущую рабочую память (`null`, если активной задачи нет). */
    suspend fun load(): WorkingMemory?

    /** Перезаписать текущую рабочую память. */
    suspend fun save(memory: WorkingMemory)

    /** Стереть рабочую память — задача завершена. */
    suspend fun clear()
}
