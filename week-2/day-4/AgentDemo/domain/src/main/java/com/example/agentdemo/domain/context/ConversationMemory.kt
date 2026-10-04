package com.example.agentdemo.domain.context

/**
 * Состояние сжатия контекста — живёт ОТДЕЛЬНО от самой ленты диалога.
 *
 * Идея задания: последние N сообщений храним как есть, а всё более старое
 * заменяем на [summary]. Чтобы знать, какая часть ленты уже «свёрнута», держим
 * границу [foldedCount].
 *
 * @param summary краткое содержание свёрнутой части диалога (пустая строка —
 *   ещё ничего не сворачивали).
 * @param foldedCount сколько самых старых сообщений ленты уже учтены в [summary].
 *   Сообщения с индексами `[0, foldedCount)` в запрос к модели больше не идут —
 *   их заменяет [summary].
 */
data class ConversationMemory(
    val summary: String = "",
    val foldedCount: Int = 0,
) {
    val hasSummary: Boolean get() = summary.isNotBlank()
}
