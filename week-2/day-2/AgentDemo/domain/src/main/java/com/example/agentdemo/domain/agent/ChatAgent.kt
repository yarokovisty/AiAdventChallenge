package com.example.agentdemo.domain.agent

import com.example.agentdemo.domain.llm.LlmClient
import com.example.agentdemo.domain.llm.LlmMessage
import com.example.agentdemo.domain.llm.LlmRequest
import com.example.agentdemo.domain.model.AgentReply
import com.example.agentdemo.domain.model.Message
import com.example.agentdemo.domain.model.Role

/**
 * Базовый чат-агент.
 *
 * Инкапсулирует поведение агента: из своей роли ([systemPrompt]), истории
 * диалога и нового запроса собирает обращение к модели [model], вызывает
 * [llmClient] и отдаёт очищенный ответ. Собственного состояния агент не держит —
 * контекст беседы передаётся снаружи параметром [ask]. Благодаря этому вся
 * «логика запроса и ответа» живёт в одном месте, отдельно от UI и сети.
 */
class ChatAgent(
    private val llmClient: LlmClient,
    override val name: String,
    override val role: String,
    private val systemPrompt: String,
    private val model: String,
) : Agent {

    override suspend fun ask(query: String, history: List<Message>): AgentReply {
        val messages = buildList {
            add(LlmMessage(role = "system", content = systemPrompt))
            // История диалога — чтобы модель помнила контекст прошлых реплик.
            history.forEach { add(LlmMessage(role = it.role.toLlmRole(), content = it.text)) }
            add(LlmMessage(role = "user", content = query))
        }
        val answer = llmClient.complete(LlmRequest(model = model, messages = messages))
        return AgentReply(text = answer.trim())
    }

    private fun Role.toLlmRole(): String = when (this) {
        Role.USER -> "user"
        Role.AGENT -> "assistant"
    }
}
