package com.example.agentdemo.domain.usecase

import com.example.agentdemo.domain.context.ContextStats
import com.example.agentdemo.domain.context.Facts
import com.example.agentdemo.domain.model.AgentReply

/**
 * Результат одной отправки с учётом стратегии управления контекстом.
 *
 * @param reply ответ агента.
 * @param facts актуальные факты после обработки запроса (для Sticky Facts они
 *   могли обновиться; для остальных стратегий возвращаются без изменений).
 * @param stats что ушло в модель — для сравнения стратегий по токенам и стабильности.
 */
data class AskOutcome(
    val reply: AgentReply,
    val facts: Facts,
    val stats: ContextStats,
)
