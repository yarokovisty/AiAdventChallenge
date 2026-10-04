package com.example.agentdemo.domain.context

/**
 * Что ушло в модель на последней отправке — для наглядного сравнения режимов
 * «со сжатием» и «без сжатия».
 *
 * @param compressionEnabled был ли включён режим сжатия.
 * @param sentMessages сколько реплик ленты ушло в модель «как есть».
 * @param foldedMessages сколько самых старых реплик заменено на summary.
 * @param usedSummary подставлялся ли summary в запрос.
 * @param estimatedTokens грубая оценка токенов контекста (summary + хвост + запрос);
 *   системный промпт одинаков в обоих режимах и в метрику не входит.
 */
data class ContextStats(
    val compressionEnabled: Boolean,
    val sentMessages: Int,
    val foldedMessages: Int,
    val usedSummary: Boolean,
    val estimatedTokens: Int,
)
