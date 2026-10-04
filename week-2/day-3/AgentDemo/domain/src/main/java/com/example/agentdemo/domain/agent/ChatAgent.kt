package com.example.agentdemo.domain.agent

import com.example.agentdemo.domain.llm.LlmClient
import com.example.agentdemo.domain.llm.LlmMessage
import com.example.agentdemo.domain.llm.LlmRequest
import com.example.agentdemo.domain.model.AgentReply
import com.example.agentdemo.domain.model.Message
import com.example.agentdemo.domain.model.Role
import com.example.agentdemo.domain.token.TokenEstimator

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
    private val tokenEstimator: TokenEstimator,
    override val name: String,
    override val role: String,
    private val systemPrompt: String,
    private val model: String,
) : Agent {

    override suspend fun ask(query: String, history: List<Message>): AgentReply {
        val result = llmClient.complete(
            LlmRequest(model = model, messages = buildMessages(query, history)),
        )
        return AgentReply(text = result.text.trim(), usage = result.usage)
    }

    override fun estimateRequestTokens(query: String, history: List<Message>): Int =
        tokenEstimator.estimateRequest(buildMessages(query, history))

    /**
     * Собирает запрос к модели: system-промпт, затем вся история, затем новый
     * запрос. Вынесено отдельно, чтобы [ask] и [estimateRequestTokens] считали
     * ровно один и тот же набор сообщений.
     */
    private fun buildMessages(query: String, history: List<Message>): List<LlmMessage> = buildList {
        add(LlmMessage(role = "system", content = systemPrompt))
        // История диалога — чтобы модель помнила контекст прошлых реплик.
        history.forEach { add(LlmMessage(role = it.role.toLlmRole(), content = it.text)) }
        add(LlmMessage(role = "user", content = query))
    }

    private fun Role.toLlmRole(): String = when (this) {
        Role.USER -> "user"
        Role.AGENT -> "assistant"
    }
}
