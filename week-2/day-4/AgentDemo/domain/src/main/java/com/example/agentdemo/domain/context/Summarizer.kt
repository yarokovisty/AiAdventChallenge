package com.example.agentdemo.domain.context

import com.example.agentdemo.domain.model.Message

/**
 * Порт сжатия части диалога в краткое содержание (summary).
 *
 * Домен объявляет контракт, а реализацию (обращение к LLM) поставляет слой
 * data — тот же приём инверсии зависимостей, что и с
 * [com.example.agentdemo.domain.llm.LlmClient].
 */
interface Summarizer {

    /**
     * Обновляет краткое содержание: вплетает [newMessages] в уже накопленный
     * [previousSummary] и возвращает новый единый summary.
     *
     * @param previousSummary summary ранее свёрнутых сообщений (может быть пустым).
     * @param newMessages очередная пачка старых сообщений, которую пора свернуть.
     */
    suspend fun summarize(previousSummary: String, newMessages: List<Message>): String
}
