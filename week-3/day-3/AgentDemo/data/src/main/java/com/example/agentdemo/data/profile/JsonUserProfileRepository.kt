package com.example.agentdemo.data.profile

import android.content.Context
import com.example.agentdemo.domain.profile.UserProfile
import com.example.agentdemo.domain.profile.UserProfileRepository
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import java.io.File

/**
 * Профили пользователя в отдельном JSON-файле (`filesDir/user_profiles.json`).
 *
 * Реализация порта [UserProfileRepository]. Слой персонализации физически
 * отделён от памяти (диалога, рабочей и долговременной). При первом запуске
 * засевает два демонстрационных профиля с разными предпочтениями, чтобы сразу
 * было чем проверить «ответы для разных профилей» (и чтобы не упираться в
 * ограничение adb на ввод кириллицы). Операции ввода-вывода — на [Dispatchers.IO].
 */
class JsonUserProfileRepository(
    context: Context,
    private val json: Json,
) : UserProfileRepository {

    private val file: File = File(context.filesDir, FILE_NAME)
    private val serializer = StoredProfiles.serializer()

    override suspend fun loadAll(): List<UserProfile> = withContext(Dispatchers.IO) {
        read().profiles.map { it.toDomain() }
    }

    override suspend fun activeId(): String? = withContext(Dispatchers.IO) {
        read().activeId
    }

    override suspend fun save(profile: UserProfile) = withContext(Dispatchers.IO) {
        val current = read()
        val updated = current.profiles.filterNot { it.id == profile.id } + profile.toStored()
        // Первый сохранённый профиль автоматически становится активным.
        val active = current.activeId ?: profile.id
        write(current.copy(activeId = active, profiles = updated))
    }

    override suspend fun delete(id: String) = withContext(Dispatchers.IO) {
        val current = read()
        val remaining = current.profiles.filterNot { it.id == id }
        val active = when {
            current.activeId != id -> current.activeId          // удалили не активный
            else -> remaining.firstOrNull()?.id                 // активный ушёл — берём первый
        }
        write(current.copy(activeId = active, profiles = remaining))
    }

    override suspend fun setActive(id: String) = withContext(Dispatchers.IO) {
        val current = read()
        if (current.profiles.any { it.id == id }) {
            write(current.copy(activeId = id))
        }
        Unit
    }

    /** Читает файл; при первом запуске создаёт и сохраняет демонстрационные профили. */
    private fun read(): StoredProfiles {
        if (!file.exists()) {
            val seeded = seedDefaults()
            write(seeded)
            return seeded
        }
        return runCatching { json.decodeFromString(serializer, file.readText()) }
            .getOrDefault(StoredProfiles()) // битый файл не должен ронять старт
    }

    private fun write(data: StoredProfiles) {
        file.writeText(json.encodeToString(serializer, data))
    }

    private fun seedDefaults(): StoredProfiles {
        val expert = StoredUserProfile(
            id = "seed-expert",
            name = "Senior-разработчик",
            persona = "Опытный Android-разработчик, хорошо знает Kotlin и архитектуру.",
            style = "Технический, по делу, без воды и лишних вступлений.",
            format = "Кратко: 2–4 предложения или компактный список; при необходимости — пример кода.",
            constraints = "Не объясняй базовые вещи, не извиняйся, отвечай по-русски.",
        )
        val novice = StoredUserProfile(
            id = "seed-novice",
            name = "Новичок",
            persona = "Только начинает программировать, термины пока незнакомы.",
            style = "Дружелюбный и ободряющий, простым языком, с аналогиями из жизни.",
            format = "Пошагово, развёрнуто; каждый термин поясняй в скобках.",
            constraints = "Избегай жаргона без объяснения, не используй код сложнее примера, отвечай по-русски.",
        )
        return StoredProfiles(activeId = expert.id, profiles = listOf(expert, novice))
    }

    private companion object {
        const val FILE_NAME = "user_profiles.json"
    }
}
