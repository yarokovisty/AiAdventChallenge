package com.example.agentdemo.domain.model

/**
 * Результат обработки запроса агентом.
 *
 * @param text ответ модели.
 * @param usage точный расход токенов за этот запрос (если сервер его вернул).
 */
data class AgentReply(
    val text: String,
    val usage: TokenUsage? = null,
)
