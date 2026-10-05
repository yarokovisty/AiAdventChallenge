package com.example.agentdemo.domain.agent

import com.example.agentdemo.domain.llm.LlmClient
import com.example.agentdemo.domain.llm.LlmMessage
import com.example.agentdemo.domain.llm.LlmRequest
import com.example.agentdemo.domain.memory.KnowledgeKind
import com.example.agentdemo.domain.memory.MemoryContext
import com.example.agentdemo.domain.model.AgentReply
import com.example.agentdemo.domain.model.Message
import com.example.agentdemo.domain.model.Role

/**
 * Базовый чат-агент.
 *
 * Инкапсулирует поведение агента: из своей роли ([systemPrompt]), памяти
 * ([MemoryContext]), истории диалога и нового запроса собирает обращение к модели
 * [model], вызывает [llmClient] и отдаёт очищенный ответ. Собственного состояния
 * агент не держит — и контекст беседы, и память передаются снаружи в [ask].
 *
 * Разные слои памяти попадают в запрос по-разному: долговременная и рабочая —
 * в системный промпт (постоянный контекст и контекст задачи), краткосрочная
 * (история) — в список сообщений.
 */
class ChatAgent(
    private val llmClient: LlmClient,
    override val name: String,
    override val role: String,
    private val systemPrompt: String,
    private val model: String,
) : Agent {

    override suspend fun ask(
        query: String,
        history: List<Message>,
        memory: MemoryContext,
    ): AgentReply {
        val messages = buildList {
            add(LlmMessage(role = "system", content = buildSystemPrompt(memory)))
            // История диалога — чтобы модель помнила контекст прошлых реплик.
            history.forEach { add(LlmMessage(role = it.role.toLlmRole(), content = it.text)) }
            add(LlmMessage(role = "user", content = query))
        }
        val answer = llmClient.complete(LlmRequest(model = model, messages = messages))
        return AgentReply(text = answer.trim())
    }

    /**
     * Собирает системный промпт: базовая роль + блоки памяти. Долговременная
     * память идёт как постоянный контекст, рабочая — как контекст текущей задачи.
     * Если память пуста, промпт не меняется относительно исходной роли.
     */
    private fun buildSystemPrompt(memory: MemoryContext): String = buildString {
        append(systemPrompt)

        if (memory.longTerm.isNotEmpty()) {
            append("\n\n# Долговременная память (помни это всегда)")
            appendFacts("Профиль пользователя", memory.longTerm, KnowledgeKind.PROFILE)
            appendFacts("Принятые решения", memory.longTerm, KnowledgeKind.DECISION)
            appendFacts("Знания", memory.longTerm, KnowledgeKind.KNOWLEDGE)
        }

        memory.working?.let { working ->
            append("\n\n# Рабочая память (контекст текущей задачи)")
            append("\nТекущая задача: ").append(working.task)
            if (working.notes.isNotEmpty()) {
                append("\nЗаметки по задаче:")
                working.notes.forEach { append("\n- ").append(it) }
            }
        }
    }

    private fun StringBuilder.appendFacts(
        title: String,
        facts: List<com.example.agentdemo.domain.memory.LongTermFact>,
        kind: KnowledgeKind,
    ) {
        val group = facts.filter { it.kind == kind }
        if (group.isEmpty()) return
        append("\n").append(title).append(":")
        group.forEach { append("\n- ").append(it.text) }
    }

    private fun Role.toLlmRole(): String = when (this) {
        Role.USER -> "user"
        Role.AGENT -> "assistant"
    }
}
