package com.example.agentdemo.domain.model

/** Одно сообщение в ленте диалога на экране. */
data class Message(
    val role: Role,
    val text: String,
)
