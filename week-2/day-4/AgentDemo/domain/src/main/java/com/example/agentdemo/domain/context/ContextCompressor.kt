package com.example.agentdemo.domain.context

import com.example.agentdemo.domain.model.Message

/**
 * Доменный сервис управления контекстом.
 *
 * Держит последние [ContextPolicy.keepLastN] сообщений «как есть», а всё более
 * старое сворачивает в [ConversationMemory.summary] — пачками по
 * [ContextPolicy.summarizeEveryM] сообщений (чтобы не пересчитывать summary на
 * каждой реплике).
 *
 * Результат — обновлённая память и «хвост» ленты, который реально уйдёт в модель
 * вместо полной истории.
 */
class ContextCompressor(
    private val summarizer: Summarizer,
    private val policy: ContextPolicy,
) {

    /** Итог применения политики к истории. */
    data class Result(
        val memory: ConversationMemory,
        /** Сообщения, которые уйдут в модель «как есть» (хвост после свёрнутой части). */
        val tail: List<Message>,
    )

    /**
     * @param history вся лента диалога ДО нового запроса пользователя.
     * @param memory текущее состояние сжатия.
     */
    suspend fun compact(history: List<Message>, memory: ConversationMemory): Result {
        // Сколько самых старых сообщений в принципе можно свернуть, не трогая
        // хвост из keepLastN «свежих» реплик.
        val foldable = (history.size - policy.keepLastN).coerceAtLeast(0)

        // Ре-саммари только когда накопилась целая пачка новых «старых» сообщений —
        // ровно идея «сворачиваем каждые M сообщений».
        val pending = foldable - memory.foldedCount
        val updated = if (pending >= policy.summarizeEveryM) {
            val newlyFolded = history.subList(memory.foldedCount, foldable)
            val newSummary = summarizer.summarize(memory.summary, newlyFolded)
            ConversationMemory(summary = newSummary, foldedCount = foldable)
        } else {
            memory
        }

        // В модель уходит summary (если есть) + всё, что после границы свёртки.
        val tail = history.drop(updated.foldedCount)
        return Result(memory = updated, tail = tail)
    }
}
