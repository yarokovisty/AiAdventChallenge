package com.example.agentdemo.domain.context

/**
 * Что ушло в модель на последней отправке — для наглядного сравнения стратегий
 * по стабильности и расходу токенов.
 *
 * @param strategy какая стратегия применялась.
 * @param sentMessages сколько реплик истории реально ушло в модель.
 * @param totalMessages сколько реплик всего было в истории (чтобы видеть, сколько
 *   отброшено окном).
 * @param usedFacts подставлялся ли блок фактов.
 * @param factsCount сколько фактов в блоке.
 * @param estimatedTokens грубая оценка токенов контекста (факты + отправленные
 *   реплики + запрос); системный промпт одинаков у всех стратегий и в метрику не входит.
 */
data class ContextStats(
    val strategy: ContextStrategy,
    val sentMessages: Int,
    val totalMessages: Int,
    val usedFacts: Boolean,
    val factsCount: Int,
    val estimatedTokens: Int,
)
