package com.example.agentdemo.domain.agent

import com.example.agentdemo.domain.llm.LlmClient
import com.example.agentdemo.domain.llm.LlmMessage
import com.example.agentdemo.domain.llm.LlmRequest
import com.example.agentdemo.domain.memory.KnowledgeKind
import com.example.agentdemo.domain.memory.MemoryContext
import com.example.agentdemo.domain.model.AgentReply
import com.example.agentdemo.domain.model.Message
import com.example.agentdemo.domain.model.Role
import com.example.agentdemo.domain.task.TaskStage
import com.example.agentdemo.domain.task.TaskState

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
 * (история) — в список сообщений. Поверх памяти в системный промпт встраивается
 * профиль пользователя (персонализация) — он задаёт, *как* отвечать.
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
     * Собирает системный промпт: базовая роль + профиль + блоки памяти.
     *
     * Профиль пользователя идёт сразу после роли — он управляет формой всего
     * ответа (стиль/формат/ограничения), поэтому должен быть виден модели раньше
     * фактов. Долговременная память — постоянный контекст, рабочая — контекст
     * текущей задачи. Если и профиль, и память пусты, промпт не меняется
     * относительно исходной роли.
     */
    private fun buildSystemPrompt(memory: MemoryContext): String = buildString {
        append(systemPrompt)

        memory.profile?.takeIf { it.hasPreferences }?.let { profile ->
            append("\n\n# Профиль пользователя (персонализация — учитывай всегда и автоматически)")
            append("\nАктивный профиль: ").append(profile.name)
            appendField("Кто пользователь", profile.persona)
            appendField("Стиль ответа", profile.style)
            appendField("Формат ответа", profile.format)
            appendField("Ограничения", profile.constraints)
            append("\nПодстраивай каждый ответ под эти предпочтения, даже если пользователь ")
            append("не повторяет их в сообщении.")
        }

        memory.task?.let { appendTaskState(it) }

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

    /**
     * Блок состояния задачи (конечный автомат). Описывает текущий этап, шаг и
     * ожидаемое действие, а при паузе — явный запрет продолжать выполнение.
     * Журнал даёт полный контекст пройденного пути, поэтому после паузы или
     * перезапуска агент продолжает с того же шага без повторных объяснений.
     */
    private fun StringBuilder.appendTaskState(task: TaskState) {
        append("\n\n# Состояние задачи (конечный автомат — ориентируйся на него)")
        append("\nЦель задачи: ").append(task.goal)
        append("\nЭтапы: ")
        append(TaskStage.entries.joinToString(" → ") { it.title })
        append("\nТекущий этап: ").append(task.stage.title)
            .append(" (").append(task.stage.slug).append(")")
        append("\nТекущий шаг: ").append(task.currentStep)
        append("\nОжидаемое действие: ").append(task.expectedAction)
        if (task.isPaused) {
            append("\nСТАТУС: задача приостановлена (пауза) на этом шаге — пользователь мог ")
            append("отойти и вернуться позже. Когда просят продолжить, продолжай ровно с этого ")
            append("шага, не начиная задачу заново и не переспрашивая уже известный контекст.")
        }
        if (task.log.isNotEmpty()) {
            append("\nЖурнал задачи:")
            task.log.forEach { append("\n- ").append(it) }
        }
        append("\nВеди себя соответственно текущему этапу. При продолжении не проси ")
        append("повторять уже известный контекст — он весь приведён выше.")
    }

    private fun StringBuilder.appendField(title: String, value: String) {
        if (value.isBlank()) return
        append("\n").append(title).append(": ").append(value.trim())
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
