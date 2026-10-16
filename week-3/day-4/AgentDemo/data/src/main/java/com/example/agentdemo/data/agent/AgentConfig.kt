package com.example.agentdemo.data.agent

/** Конфигурация агента по умолчанию: роль (системный промпт) и модель Z.ai. */
internal object AgentConfig {
    const val NAME = "Ассистент"
    const val ROLE = "Универсальный ассистент"
    const val MODEL = "glm-4.6"

    val SYSTEM_PROMPT = """
        Ты — вежливый универсальный ассистент. Отвечай кратко, ясно и по делу на русском языке.
        Если вопрос неоднозначен — уточни. Не выдумывай факты.
    """.trimIndent()
}
