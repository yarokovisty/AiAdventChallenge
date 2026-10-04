package com.example.agentdemo.data.remote.dto

import kotlinx.serialization.Serializable

/** Тело запроса к Z.ai (OpenAI-совместимый формат chat/completions). */
@Serializable
data class ChatRequestDto(
    val model: String,
    val messages: List<ChatMessageDto>,
    val temperature: Double,
    // Отключаем «размышление»: для простого агента нужен прямой ответ без reasoning-трейса.
    val thinking: ThinkingDto? = null,
)

@Serializable
data class ChatMessageDto(
    val role: String,
    val content: String,
)

@Serializable
data class ThinkingDto(
    val type: String,
)

/** Ответ Z.ai: либо choices с ответом, либо объект error. */
@Serializable
data class ChatResponseDto(
    val choices: List<ChoiceDto> = emptyList(),
    val usage: UsageDto? = null,
    val error: ErrorDto? = null,
)

@Serializable
data class ChoiceDto(
    val message: ChatMessageDto,
)

/** Точный расход токенов, посчитанный сервером (OpenAI-совместимое поле `usage`). */
@Serializable
data class UsageDto(
    val prompt_tokens: Int = 0,
    val completion_tokens: Int = 0,
    val total_tokens: Int = 0,
    val prompt_tokens_details: PromptTokensDetailsDto? = null,
)

@Serializable
data class PromptTokensDetailsDto(
    val cached_tokens: Int = 0,
)

@Serializable
data class ErrorDto(
    val code: String? = null,
    val message: String? = null,
)
