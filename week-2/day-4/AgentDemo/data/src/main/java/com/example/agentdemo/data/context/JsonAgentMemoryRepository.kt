package com.example.agentdemo.data.context

import android.content.Context
import com.example.agentdemo.domain.context.AgentMemoryRepository
import com.example.agentdemo.domain.context.ConversationMemory
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import java.io.File

/**
 * Хранит состояние сжатия ([ConversationMemory]) в отдельном JSON-файле
 * (`filesDir/agent_memory.json`) — рядом с историей диалога, но независимо от неё.
 *
 * Реализация порта [AgentMemoryRepository]. Отдельный файл подчёркивает идею
 * задания: summary хранится ОТДЕЛЬНО от полной истории. Все операции ввода-вывода
 * уходят на [Dispatchers.IO].
 */
class JsonAgentMemoryRepository(
    context: Context,
    private val json: Json,
) : AgentMemoryRepository {

    private val file: File = File(context.filesDir, FILE_NAME)

    override suspend fun load(): ConversationMemory = withContext(Dispatchers.IO) {
        if (!file.exists()) return@withContext ConversationMemory()
        runCatching {
            json.decodeFromString(StoredMemory.serializer(), file.readText()).toDomain()
        }.getOrDefault(ConversationMemory()) // битый/несовместимый файл не должен ронять старт
    }

    override suspend fun save(memory: ConversationMemory) = withContext(Dispatchers.IO) {
        file.writeText(json.encodeToString(StoredMemory.serializer(), memory.toStored()))
    }

    override suspend fun clear() = withContext(Dispatchers.IO) {
        file.delete()
        Unit
    }

    private companion object {
        const val FILE_NAME = "agent_memory.json"
    }
}
