package com.example.agentdemo.data.task

import android.content.Context
import com.example.agentdemo.domain.task.TaskState
import com.example.agentdemo.domain.task.TaskStateRepository
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import java.io.File

/**
 * Состояние задачи в отдельном JSON-файле (`filesDir/task_state.json`).
 *
 * Реализация порта [TaskStateRepository]. Хранит ровно один активный снимок
 * автомата; отсутствие файла означает, что активной задачи нет. Персистентность
 * и есть механизм паузы: состояние переживает закрытие приложения, поэтому после
 * перезапуска задачу можно продолжить с того же шага. Ввод-вывод — на [Dispatchers.IO].
 */
class JsonTaskStateRepository(
    context: Context,
    private val json: Json,
) : TaskStateRepository {

    private val file: File = File(context.filesDir, FILE_NAME)
    private val serializer = StoredTaskState.serializer()

    override suspend fun load(): TaskState? = withContext(Dispatchers.IO) {
        if (!file.exists()) return@withContext null
        runCatching { json.decodeFromString(serializer, file.readText()).toDomain() }
            .getOrNull()
    }

    override suspend fun save(state: TaskState) = withContext(Dispatchers.IO) {
        file.writeText(json.encodeToString(serializer, state.toStored()))
    }

    override suspend fun clear() = withContext(Dispatchers.IO) {
        file.delete()
        Unit
    }

    private companion object {
        const val FILE_NAME = "task_state.json"
    }
}
