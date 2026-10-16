package com.example.agentdemo.data.memory

import android.content.Context
import com.example.agentdemo.domain.memory.WorkingMemory
import com.example.agentdemo.domain.memory.WorkingMemoryRepository
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import java.io.File

/**
 * Рабочая память в отдельном JSON-файле (`filesDir/working_memory.json`).
 *
 * Реализация порта [WorkingMemoryRepository]. Хранит ровно одну активную задачу;
 * отсутствие файла означает, что задачи нет. Отдельное хранилище от диалога и
 * долговременной памяти. Операции ввода-вывода уходят на [Dispatchers.IO].
 */
class JsonWorkingMemoryRepository(
    context: Context,
    private val json: Json,
) : WorkingMemoryRepository {

    private val file: File = File(context.filesDir, FILE_NAME)
    private val serializer = StoredWorkingMemory.serializer()

    override suspend fun load(): WorkingMemory? = withContext(Dispatchers.IO) {
        if (!file.exists()) return@withContext null
        runCatching { json.decodeFromString(serializer, file.readText()).toDomain() }
            .getOrNull()
    }

    override suspend fun save(memory: WorkingMemory) = withContext(Dispatchers.IO) {
        file.writeText(json.encodeToString(serializer, memory.toStored()))
    }

    override suspend fun clear() = withContext(Dispatchers.IO) {
        file.delete()
        Unit
    }

    private companion object {
        const val FILE_NAME = "working_memory.json"
    }
}
