package com.example.agentdemo.domain.llm

import com.example.agentdemo.domain.model.TokenUsage

/**
 * Ответ LLM-клиента: текст модели плюс расход токенов из поля `usage`.
 *
 * Раньше клиент возвращал голую строку; теперь он отдаёт ещё и [usage],
 * чтобы агент и UI могли показывать точный расход токенов и стоимость.
 *
 * @param usage `null`, если сервер не прислал `usage` (например, при ошибке).
 */
data class LlmResult(
    val text: String,
    val usage: TokenUsage?,
)
