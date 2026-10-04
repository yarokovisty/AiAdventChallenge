package com.example.agentdemo.domain.model

/**
 * Точный расход токенов за один запрос — приходит от сервера в поле `usage`.
 *
 * В отличие от локальной оценки ([com.example.agentdemo.domain.token.TokenEstimator]),
 * эти цифры считает сам токенизатор модели, поэтому они авторитетны.
 *
 * @param promptTokens токены входа (system-промпт + вся история + новый запрос).
 * @param completionTokens токены ответа модели.
 * @param totalTokens сумма входа и выхода — именно за столько выставляется счёт.
 * @param cachedTokens часть [promptTokens], обслуженная из кеша (дешевле).
 */
data class TokenUsage(
    val promptTokens: Int,
    val completionTokens: Int,
    val totalTokens: Int,
    val cachedTokens: Int = 0,
)
