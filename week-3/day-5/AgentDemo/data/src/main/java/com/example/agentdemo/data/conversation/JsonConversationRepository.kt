package com.example.agentdemo.data.conversation

import android.content.Context
import com.example.agentdemo.domain.conversation.ConversationRepository
import com.example.agentdemo.domain.model.Message
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.json.Json
import java.io.File

/**
 * Хранит историю диалога в одном JSON-файле во внутренней памяти приложения
 * (`filesDir/conversation.json`).
 *
 * Реализация порта [ConversationRepository]. Запись — перезапись всего списка:
 * для единственного диалога это проще и надёжнее инкрементального append (нет
 * риска рассинхрона и частичной записи). Все операции ввода-вывода уходят на
 * [Dispatchers.IO], чтобы не блокировать главный поток.
 */
class JsonConversationRepository(
    context: Context,
    private val json: Json,
) : ConversationRepository {

    private val file: File = File(context.filesDir, FILE_NAME)
    private val serializer = ListSerializer(StoredMessage.serializer())

    override suspend fun load(): List<Message> = withContext(Dispatchers.IO) {
        if (!file.exists()) return@withContext emptyList()
        runCatching {
            json.decodeFromString(serializer, file.readText()).map { it.toDomain() }
        }.getOrDefault(emptyList()) // битый/несовместимый файл не должен ронять старт
    }

    override suspend fun save(messages: List<Message>) = withContext(Dispatchers.IO) {
        file.writeText(json.encodeToString(serializer, messages.map { it.toStored() }))
    }

    override suspend fun clear() = withContext(Dispatchers.IO) {
        file.delete()
        Unit
    }

    private companion object {
        const val FILE_NAME = "conversation.json"
    }
}
