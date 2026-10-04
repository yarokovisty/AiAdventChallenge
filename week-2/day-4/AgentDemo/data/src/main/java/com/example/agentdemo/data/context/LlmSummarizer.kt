package com.example.agentdemo.data.context

import com.example.agentdemo.domain.context.Summarizer
import com.example.agentdemo.domain.llm.LlmClient
import com.example.agentdemo.domain.llm.LlmMessage
import com.example.agentdemo.domain.llm.LlmRequest
import com.example.agentdemo.domain.model.Message
import com.example.agentdemo.domain.model.Role

/**
 * Реализация порта [Summarizer] поверх [LlmClient].
 *
 * Сжатие части диалога — это тоже обращение к модели: отдаём ей старые сообщения
 * (и уже накопленный summary) и просим вернуть обновлённое краткое содержание.
 * Температуру держим низкой — нужно стабильное, фактологичное резюме, а не
 * творчество.
 */
class LlmSummarizer(
    private val llmClient: LlmClient,
    private val model: String,
) : Summarizer {

    override suspend fun summarize(previousSummary: String, newMessages: List<Message>): String {
        if (newMessages.isEmpty()) return previousSummary

        val transcript = newMessages.joinToString("\n") { msg ->
            val who = when (msg.role) {
                Role.USER -> "Пользователь"
                Role.AGENT -> "Ассистент"
            }
            "$who: ${msg.text}"
        }

        val userContent = buildString {
            if (previousSummary.isNotBlank()) {
                appendLine("Текущее краткое содержание диалога:")
                appendLine(previousSummary)
                appendLine()
                appendLine("Новые сообщения, которые нужно вплести в содержание, не теряя старых фактов:")
            } else {
                appendLine("Сообщения диалога, которые нужно кратко законспектировать:")
            }
            append(transcript)
        }

        val messages = listOf(
            LlmMessage(role = "system", content = SUMMARY_PROMPT),
            LlmMessage(role = "user", content = userContent),
        )

        return llmClient.complete(
            LlmRequest(model = model, messages = messages, temperature = 0.2),
        ).trim()
    }

    private companion object {
        val SUMMARY_PROMPT = """
            Ты сжимаешь историю диалога в краткое содержание (summary).
            Сохрани ВСЕ факты, важные для продолжения беседы: имена, числа, даты,
            предпочтения пользователя, принятые решения и открытые вопросы.
            Обновляй предыдущее содержание новыми сообщениями, не теряя старых фактов.
            Пиши сжато, по-русски, короткими пунктами. Без вступлений и пояснений —
            только само содержание.
        """.trimIndent()
    }
}
