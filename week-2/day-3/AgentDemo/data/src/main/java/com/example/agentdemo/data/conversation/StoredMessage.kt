package com.example.agentdemo.data.conversation

import com.example.agentdemo.domain.model.Message
import com.example.agentdemo.domain.model.Role
import kotlinx.serialization.Serializable

/**
 * Сериализуемое представление одного сообщения для хранения на диске.
 *
 * Отдельный DTO нужен, чтобы доменная модель [Message] оставалась чистой и не
 * тянула аннотации kotlinx.serialization: формат хранения — забота слоя data.
 */
@Serializable
data class StoredMessage(
    val role: Role,
    val text: String,
)

/** Доменное сообщение → хранимое. */
fun Message.toStored(): StoredMessage = StoredMessage(role = role, text = text)

/** Хранимое сообщение → доменное. */
fun StoredMessage.toDomain(): Message = Message(role = role, text = text)
