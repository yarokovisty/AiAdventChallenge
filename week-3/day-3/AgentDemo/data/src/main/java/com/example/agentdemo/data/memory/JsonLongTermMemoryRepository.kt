package com.example.agentdemo.data.memory

import android.content.Context
import com.example.agentdemo.domain.memory.LongTermFact
import com.example.agentdemo.domain.memory.LongTermMemoryRepository
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.json.Json
import java.io.File

/**
 * Долговременная память в отдельном JSON-файле (`filesDir/long_term_memory.json`).
 *
 * Реализация порта [LongTermMemoryRepository]. Своё хранилище, отдельное от
 * диалога (`conversation.json`) и рабочей памяти (`working_memory.json`) — слои
 * физически разделены. Операции ввода-вывода уходят на [Dispatchers.IO].
 */
class JsonLongTermMemoryRepository(
    context: Context,
    private val json: Json,
) : LongTermMemoryRepository {

    private val file: File = File(context.filesDir, FILE_NAME)
    private val serializer = ListSerializer(StoredLongTermFact.serializer())

    override suspend fun load(): List<LongTermFact> = withContext(Dispatchers.IO) {
        readAll().map { it.toDomain() }
    }

    override suspend fun add(fact: LongTermFact) = withContext(Dispatchers.IO) {
        writeAll(readAll() + fact.toStored())
    }

    override suspend fun remove(id: String) = withContext(Dispatchers.IO) {
        writeAll(readAll().filterNot { it.id == id })
    }

    override suspend fun clear() = withContext(Dispatchers.IO) {
        file.delete()
        Unit
    }

    private fun readAll(): List<StoredLongTermFact> {
        if (!file.exists()) return emptyList()
        return runCatching { json.decodeFromString(serializer, file.readText()) }
            .getOrDefault(emptyList()) // битый файл не должен ронять старт
    }

    private fun writeAll(facts: List<StoredLongTermFact>) {
        file.writeText(json.encodeToString(serializer, facts))
    }

    private companion object {
        const val FILE_NAME = "long_term_memory.json"
    }
}
