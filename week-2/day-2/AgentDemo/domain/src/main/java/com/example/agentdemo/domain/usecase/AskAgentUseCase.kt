package com.example.agentdemo.domain.usecase

import com.example.agentdemo.domain.agent.Agent
import com.example.agentdemo.domain.model.AgentReply
import com.example.agentdemo.domain.model.Message

/**
 * Единственный сценарий фичи: отправить запрос пользователя агенту.
 *
 * UI зависит от use case, а не от агента напрямую. Здесь же — валидация входа
 * и перевод исключений в [Result], чтобы презентационный слой не ловил ошибки сам.
 *
 * @param history предыдущие сообщения диалога (без нового [query]).
 */
class AskAgentUseCase(private val agent: Agent) {

    suspend operator fun invoke(
        query: String,
        history: List<Message> = emptyList(),
    ): Result<AgentReply> {
        val trimmed = query.trim()
        if (trimmed.isEmpty()) {
            return Result.failure(IllegalArgumentException("Запрос пуст"))
        }
        return runCatching { agent.ask(trimmed, history) }
    }
}
