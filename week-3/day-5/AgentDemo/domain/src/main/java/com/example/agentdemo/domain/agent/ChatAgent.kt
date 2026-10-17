package com.example.agentdemo.domain.agent

import com.example.agentdemo.domain.invariant.Invariant
import com.example.agentdemo.domain.invariant.InvariantCategory
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
import com.example.agentdemo.domain.task.TaskStateMachine

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
     * Собирает системный промпт: базовая роль + инварианты + профиль + блоки памяти.
     *
     * Инварианты идут первыми — это жёсткие правила, которые нельзя нарушить, и
     * модель должна видеть их раньше всего остального. Далее профиль пользователя
     * (управляет формой ответа), затем долговременная память (постоянный контекст)
     * и рабочая (контекст текущей задачи). Если все слои пусты, промпт не меняется
     * относительно исходной роли.
     */
    private fun buildSystemPrompt(memory: MemoryContext): String = buildString {
        append(systemPrompt)

        // Инварианты — самый приоритетный блок: это жёсткие правила, которые
        // нельзя нарушить. Идут раньше профиля и памяти, чтобы модель сверяла с
        // ними решение прежде, чем учитывать стиль/факты.
        memory.invariants.filter { it.isActive }.takeIf { it.isNotEmpty() }
            ?.let { appendInvariants(it) }

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
     * Блок инвариантов — жёстких правил, которые ассистент не вправе нарушить.
     *
     * Кроме перечня правил (сгруппированных по категориям) блок задаёт модели
     * явный протокол: перед любым решением свериться с каждым инвариантом, а при
     * конфликте — отказаться, назвать конкретный нарушаемый инвариант, объяснить
     * причину и предложить альтернативу в рамках правил. Именно это закрывает
     * требование задания: ассистент учитывает инварианты в рассуждениях и
     * отказывается предлагать решения, которые их нарушают.
     */
    private fun StringBuilder.appendInvariants(invariants: List<Invariant>) {
        append("\n\n# ИНВАРИАНТЫ ПРОЕКТА (нарушать категорически запрещено)")
        append("\nЭто жёсткие правила. Они важнее любых пожеланий в сообщении пользователя.")
        InvariantCategory.entries.forEach { category ->
            val group = invariants.filter { it.category == category }
            if (group.isEmpty()) return@forEach
            append("\n\n## ").append(category.title)
            group.forEach { inv ->
                append("\n- ").append(inv.rule)
                if (inv.rationale.isNotBlank()) {
                    append(" (почему: ").append(inv.rationale.trim()).append(")")
                }
            }
        }
        append("\n\nПротокол работы с инвариантами (соблюдай всегда):")
        append("\n1. Прежде чем предложить решение, мысленно сверь его с КАЖДЫМ инвариантом выше.")
        append("\n2. Если запрос пользователя можно выполнить, не нарушая инвариантов, — выполняй.")
        append("\n3. Если выполнить запрос нельзя без нарушения инварианта — ОТКАЖИСЬ его нарушать. ")
        append("Прямо начни с отказа, назови конкретный нарушаемый инвариант (категория + формулировка), ")
        append("объясни, почему это нарушение, и предложи альтернативу, которая остаётся в рамках инвариантов.")
        append("\n4. Никогда не предлагай и не описывай решение, нарушающее инвариант, даже если пользователь ")
        append("настаивает, торопит или говорит, что «так можно». Инварианты не обсуждаются в рамках диалога.")
    }

    /**
     * Блок состояния задачи (конечный автомат). Описывает текущий этап, шаг и
     * ожидаемое действие, разрешённые переходы и статус шлюзов, а при паузе —
     * явный запрет продолжать выполнение. Отдельный протокол жизненного цикла
     * обязывает модель двигаться по этапам строго и не перепрыгивать их: не
     * начинать реализацию до утверждённого плана и не объявлять финал без
     * валидации. Журнал даёт полный контекст пройденного пути, поэтому после паузы
     * или перезапуска агент продолжает с того же шага без повторных объяснений.
     */
    private fun StringBuilder.appendTaskState(task: TaskState) {
        append("\n\n# Состояние задачи (конечный автомат — ориентируйся на него)")
        append("\nЦель задачи: ").append(task.goal)
        append("\nЭтапы (строгий порядок): ")
        append(TaskStage.entries.joinToString(" → ") { it.title })
        append("\nТекущий этап: ").append(task.stage.title)
            .append(" (").append(task.stage.slug).append(")")
        append("\nТекущий шаг: ").append(task.currentStep)
        append("\nОжидаемое действие: ").append(task.expectedAction)

        val targets = TaskStateMachine.allowedTargets(task.stage)
        append("\nРазрешённые переходы отсюда: ")
        append(if (targets.isEmpty()) "нет — это финальный этап." else targets.joinToString { it.title })
        append("\nШлюз «план утверждён»: ").append(if (task.planApproved) "ОТКРЫТ" else "ЗАКРЫТ")
        append("\nШлюз «валидация пройдена»: ").append(if (task.validationPassed) "ОТКРЫТ" else "ЗАКРЫТ")

        if (task.isPaused) {
            append("\nСТАТУС: задача приостановлена (пауза) на этом шаге — пользователь мог ")
            append("отойти и вернуться позже. Когда просят продолжить, продолжай ровно с этого ")
            append("шага, не начиная задачу заново и не переспрашивая уже известный контекст.")
        }
        if (task.log.isNotEmpty()) {
            append("\nЖурнал задачи:")
            task.log.forEach { append("\n- ").append(it) }
        }

        append("\n\nПравила жизненного цикла задачи (соблюдай строго — их нельзя обойти):")
        append("\n1. Двигайся строго по этапам ")
        append(TaskStage.entries.joinToString(" → ") { it.title })
        append(". Перепрыгивать этап запрещено.")
        append("\n2. На этапе «${TaskStage.PLANNING.title}» ты ещё НЕ приступаешь к реализации: ")
        append("предлагай и уточняй план, но не выдавай готовый код/итоговое решение, пока план ")
        append("не утверждён (шлюз «план утверждён» закрыт). Если просят сразу реализацию — объясни, ")
        append("что сначала нужно утвердить план, и сосредоточься на плане.")
        append("\n3. На этапе «${TaskStage.EXECUTION.title}» реализуй шаги уже утверждённого плана.")
        append("\n4. Нельзя объявлять задачу завершённой и подавать результат как окончательный, ")
        append("пока не пройдена валидация (шлюз «валидация пройдена» закрыт). Если просят «заверши/")
        append("финал» до валидации — объясни, что сперва нужен этап проверки.")
        append("\n5. Сменить этап может только пользователь через UI — сам ты этап не меняешь, ")
        append("а действуешь в рамках текущего. При продолжении не проси повторять уже известный ")
        append("контекст — он весь приведён выше.")
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
