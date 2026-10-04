package com.example.agentdemo.domain.usecase

import com.example.agentdemo.domain.context.ContextStats
import com.example.agentdemo.domain.context.ConversationMemory
import com.example.agentdemo.domain.model.AgentReply

/**
 * Результат одного обращения к агенту через [AskAgentUseCase].
 *
 * @param reply ответ агента.
 * @param memory обновлённое состояние сжатия — вызывающая сторона должна его
 *   сохранить, чтобы summary пережил перезапуск.
 * @param stats что ушло в модель на этой отправке (для сравнения режимов).
 */
data class AskOutcome(
    val reply: AgentReply,
    val memory: ConversationMemory,
    val stats: ContextStats,
)
