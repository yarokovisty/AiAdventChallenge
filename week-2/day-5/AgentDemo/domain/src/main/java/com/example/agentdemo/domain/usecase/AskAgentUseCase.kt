package com.example.agentdemo.domain.usecase

import com.example.agentdemo.domain.agent.Agent
import com.example.agentdemo.domain.context.ContextManager
import com.example.agentdemo.domain.context.ContextStats
import com.example.agentdemo.domain.context.ContextStrategy
import com.example.agentdemo.domain.context.Facts
import com.example.agentdemo.domain.context.FactsExtractor
import com.example.agentdemo.domain.llm.LlmMessage
import com.example.agentdemo.domain.model.Message
import com.example.agentdemo.domain.model.Role
import com.example.agentdemo.domain.token.TokenEstimator

/**
 * Единственный сценарий фичи: отправить запрос агенту, применив выбранную
 * [ContextStrategy] управления контекстом.
 *
 * Здесь сходятся три стратегии: use case обновляет факты (для Sticky Facts),
 * просит [ContextManager] нарезать историю под стратегию, вызывает агента и
 * считает оценку токенов — чтобы презентация могла сравнить стратегии.
 *
 * UI зависит от use case, а не от агента напрямую. Валидация входа и перевод
 * исключений в [Result] — тоже здесь.
 */
class AskAgentUseCase(
    private val agent: Agent,
    private val contextManager: ContextManager,
    private val factsExtractor: FactsExtractor,
    private val tokenEstimator: TokenEstimator,
) {

    /**
     * @param query новый запрос пользователя.
     * @param strategy активная стратегия управления контекстом.
     * @param history предыдущие сообщения диалога (без нового [query]).
     * @param facts накопленные липкие факты (используются и обновляются только
     *   стратегией [ContextStrategy.STICKY_FACTS]).
     */
    suspend operator fun invoke(
        query: String,
        strategy: ContextStrategy,
        history: List<Message> = emptyList(),
        facts: Facts = Facts.EMPTY,
    ): Result<AskOutcome> {
        val trimmed = query.trim()
        if (trimmed.isEmpty()) {
            return Result.failure(IllegalArgumentException("Запрос пуст"))
        }
        return runCatching {
            // Sticky Facts: факты обновляются ПОСЛЕ каждого сообщения пользователя,
            // поэтому сначала вплетаем новый запрос в блок фактов, а уже затем шлём.
            val updatedFacts = if (strategy == ContextStrategy.STICKY_FACTS) {
                factsExtractor.update(facts, trimmed, history.takeLast(contextManager.windowSize))
            } else {
                facts
            }

            val prepared = contextManager.prepare(strategy, history, updatedFacts)
            val reply = agent.ask(trimmed, prepared.history, prepared.facts)

            val stats = ContextStats(
                strategy = strategy,
                sentMessages = prepared.history.size,
                totalMessages = history.size,
                usedFacts = prepared.facts != null,
                factsCount = updatedFacts.items.size,
                estimatedTokens = estimateTokens(prepared, trimmed),
            )
            AskOutcome(reply = reply, facts = updatedFacts, stats = stats)
        }
    }

    /** Оценка токенов ровно того, что ушло бы в модель (без общего системного промпта). */
    private fun estimateTokens(
        prepared: ContextManager.PreparedContext,
        query: String,
    ): Int {
        val messages = buildList {
            prepared.facts?.let { add(LlmMessage(role = "system", content = it)) }
            prepared.history.forEach { add(LlmMessage(role = it.role.toLlmRole(), content = it.text)) }
            add(LlmMessage(role = "user", content = query))
        }
        return tokenEstimator.estimateRequest(messages)
    }

    private fun Role.toLlmRole(): String = when (this) {
        Role.USER -> "user"
        Role.AGENT -> "assistant"
    }
}
