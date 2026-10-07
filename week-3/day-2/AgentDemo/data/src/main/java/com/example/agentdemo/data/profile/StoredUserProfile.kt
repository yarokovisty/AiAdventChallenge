package com.example.agentdemo.data.profile

import com.example.agentdemo.domain.profile.UserProfile
import kotlinx.serialization.Serializable

/**
 * Сериализуемое представление профиля для хранения на диске.
 *
 * Отдельный DTO, чтобы доменная модель [UserProfile] оставалась чистой и не
 * тянула аннотации kotlinx.serialization — формат хранения живёт в слое data.
 */
@Serializable
data class StoredUserProfile(
    val id: String,
    val name: String,
    val persona: String = "",
    val style: String = "",
    val format: String = "",
    val constraints: String = "",
)

/**
 * Весь слой персонализации в одном файле: набор профилей и id активного.
 * Хранить активный id рядом с профилями проще, чем в отдельном файле, и
 * гарантирует согласованность (активный всегда указывает на существующий).
 */
@Serializable
data class StoredProfiles(
    val activeId: String? = null,
    val profiles: List<StoredUserProfile> = emptyList(),
)

fun UserProfile.toStored(): StoredUserProfile =
    StoredUserProfile(
        id = id,
        name = name,
        persona = persona,
        style = style,
        format = format,
        constraints = constraints,
    )

fun StoredUserProfile.toDomain(): UserProfile =
    UserProfile(
        id = id,
        name = name,
        persona = persona,
        style = style,
        format = format,
        constraints = constraints,
    )
