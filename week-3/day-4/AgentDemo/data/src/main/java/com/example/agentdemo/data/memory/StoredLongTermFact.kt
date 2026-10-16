package com.example.agentdemo.data.memory

import com.example.agentdemo.domain.memory.KnowledgeKind
import com.example.agentdemo.domain.memory.LongTermFact
import kotlinx.serialization.Serializable

/**
 * Сериализуемое представление факта долговременной памяти для хранения на диске.
 *
 * Отдельный DTO, чтобы доменная модель [LongTermFact] оставалась чистой и не
 * тянула аннотации kotlinx.serialization — формат хранения живёт в слое data.
 */
@Serializable
data class StoredLongTermFact(
    val id: String,
    val kind: KnowledgeKind,
    val text: String,
)

fun LongTermFact.toStored(): StoredLongTermFact =
    StoredLongTermFact(id = id, kind = kind, text = text)

fun StoredLongTermFact.toDomain(): LongTermFact =
    LongTermFact(id = id, kind = kind, text = text)
