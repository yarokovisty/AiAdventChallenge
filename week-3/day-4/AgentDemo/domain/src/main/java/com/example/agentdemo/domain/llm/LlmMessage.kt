package com.example.agentdemo.domain.llm

/** Одно сообщение в формате, который понимает LLM (роль + содержимое). */
data class LlmMessage(
    val role: String, // "system" | "user" | "assistant"
    val content: String,
)
