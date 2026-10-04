package com.example.agentdemo.data.context

import com.example.agentdemo.domain.context.ConversationMemory
import kotlinx.serialization.Serializable

/**
 * Сериализуемое представление состояния сжатия для хранения на диске.
 *
 * Отдельный DTO — чтобы доменная [ConversationMemory] оставалась чистой и не
 * тянула аннотации kotlinx.serialization (формат хранения — забота слоя data).
 */
@Serializable
data class StoredMemory(
    val summary: String = "",
    val foldedCount: Int = 0,
)

/** Доменная память → хранимая. */
fun ConversationMemory.toStored(): StoredMemory =
    StoredMemory(summary = summary, foldedCount = foldedCount)

/** Хранимая память → доменная. */
fun StoredMemory.toDomain(): ConversationMemory =
    ConversationMemory(summary = summary, foldedCount = foldedCount)
