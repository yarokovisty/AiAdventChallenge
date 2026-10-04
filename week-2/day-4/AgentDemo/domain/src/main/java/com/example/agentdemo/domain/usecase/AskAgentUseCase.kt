package com.example.agentdemo.domain.usecase

import com.example.agentdemo.domain.agent.Agent
import com.example.agentdemo.domain.context.ContextCompressor
import com.example.agentdemo.domain.context.ContextStats
import com.example.agentdemo.domain.context.ConversationMemory
import com.example.agentdemo.domain.llm.LlmMessage
import com.example.agentdemo.domain.model.Message
import com.example.agentdemo.domain.model.Role
import com.example.agentdemo.domain.token.TokenEstimator

/**
 * Единственный сценарий фичи: отправить запрос пользователя агенту — с
 * управлением контекстом.
 *
 * Здесь решается, что именно уйдёт в модель:
 * - **со сжатием** ([compress] = true) — старые сообщения заменяются на summary
 *   (считает [ContextCompressor]), в модель идёт summary + «хвост» свежих реплик;
 * - **без сжатия** — в модель уходит вся история «как есть».
 *
 * Возвращает не только ответ, но и обновлённую [ConversationMemory] (её надо
 * сохранить) и [ContextStats] для наглядного сравнения режимов.
 */
class AskAgentUseCase(
    private val agent: Agent,
    private val compressor: ContextCompressor,
    private val tokenEstimator: TokenEstimator,
) {

    suspend operator fun invoke(
        query: String,
        history: List<Message> = emptyList(),
        memory: ConversationMemory = ConversationMemory(),
        compress: Boolean = true,
    ): Result<AskOutcome> {
        val trimmed = query.trim()
        if (trimmed.isEmpty()) {
            return Result.failure(IllegalArgumentException("Запрос пуст"))
        }

        return runCatching {
            // 1. Определяем, что уйдёт в модель, исходя из режима.
            val effectiveMemory: ConversationMemory
            val tail: List<Message>
            if (compress) {
                val result = compressor.compact(history, memory)
                effectiveMemory = result.memory
                tail = result.tail
            } else {
                // Без сжатия память не трогаем, а в модель отдаём всю историю.
                effectiveMemory = memory
                tail = history
            }
            val summaryForModel =
                if (compress && effectiveMemory.hasSummary) effectiveMemory.summary else null

            // 2. Спрашиваем агента.
            val reply = agent.ask(trimmed, tail, summaryForModel)

            // 3. Считаем метрики контекста для сравнения режимов.
            val stats = ContextStats(
                compressionEnabled = compress,
                sentMessages = tail.size,
                foldedMessages = if (compress) effectiveMemory.foldedCount else 0,
                usedSummary = summaryForModel != null,
                estimatedTokens = estimateContextTokens(summaryForModel, tail, trimmed),
            )

            AskOutcome(reply = reply, memory = effectiveMemory, stats = stats)
        }
    }

    /**
     * Оценка токенов контекста запроса: summary + «хвост» + сам вопрос. Системный
     * промпт одинаков в обоих режимах, поэтому в метрику сравнения не входит —
     * сравниваем именно то, что меняется от сжатия.
     */
    private fun estimateContextTokens(
        summary: String?,
        tail: List<Message>,
        query: String,
    ): Int {
        val messages = buildList {
            if (!summary.isNullOrBlank()) add(LlmMessage("system", summary))
            tail.forEach { add(LlmMessage(it.role.toLlmRole(), it.text)) }
            add(LlmMessage("user", query))
        }
        return tokenEstimator.estimateRequest(messages)
    }

    private fun Role.toLlmRole(): String = when (this) {
        Role.USER -> "user"
        Role.AGENT -> "assistant"
    }
}
