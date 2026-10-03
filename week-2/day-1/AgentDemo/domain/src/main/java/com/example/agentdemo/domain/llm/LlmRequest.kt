package com.example.agentdemo.domain.llm

/** Запрос к LLM, который формирует агент. Транспорт (HTTP/JSON) домену неизвестен. */
data class LlmRequest(
    val model: String,
    val messages: List<LlmMessage>,
    val temperature: Double = 0.7,
)
