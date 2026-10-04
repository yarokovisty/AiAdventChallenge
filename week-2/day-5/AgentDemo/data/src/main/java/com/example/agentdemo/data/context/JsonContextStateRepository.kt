package com.example.agentdemo.data.context

import android.content.Context
import com.example.agentdemo.domain.context.ContextState
import com.example.agentdemo.domain.context.ContextStateRepository
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import java.io.File

/**
 * Хранит состояние управления контекстом (стратегия + факты + ветки) в одном
 * JSON-файле во внутренней памяти (`filesDir/context_state.json`) — рядом с
 * историей диалога из Day 2.
 *
 * Реализация порта [ContextStateRepository]. Как и у истории диалога, запись —
 * перезапись всего состояния на [Dispatchers.IO]; битый файл не роняет старт.
 */
class JsonContextStateRepository(
    context: Context,
    private val json: Json,
) : ContextStateRepository {

    private val file: File = File(context.filesDir, FILE_NAME)

    override suspend fun load(): ContextState = withContext(Dispatchers.IO) {
        if (!file.exists()) return@withContext ContextState.DEFAULT
        runCatching {
            json.decodeFromString(StoredContextState.serializer(), file.readText()).toDomain()
        }.getOrDefault(ContextState.DEFAULT)
    }

    override suspend fun save(state: ContextState) = withContext(Dispatchers.IO) {
        file.writeText(json.encodeToString(StoredContextState.serializer(), state.toStored()))
    }

    override suspend fun clear() = withContext(Dispatchers.IO) {
        file.delete()
        Unit
    }

    private companion object {
        const val FILE_NAME = "context_state.json"
    }
}
