package com.example.agentdemo.domain.agent

import com.example.agentdemo.domain.memory.MemoryContext
import com.example.agentdemo.domain.model.AgentReply
import com.example.agentdemo.domain.model.Message

/**
 * Агент — самостоятельная сущность, а не просто вызов API.
 *
 * Он владеет своей ролью (системным промптом) и инкапсулирует всю логику
 * превращения запроса пользователя в обращение к LLM и разбора ответа.
 * Внешний код взаимодействует только с методом [ask], не зная, как именно
 * агент строит запрос к модели.
 */
interface Agent {
    /** Человеческое имя агента (для шапки интерфейса). */
    val name: String

    /** Короткое описание роли агента. */
    val role: String

    /**
     * Обрабатывает запрос пользователя и возвращает ответ агента.
     *
     * @param query новый запрос пользователя.
     * @param history предыдущие сообщения диалога (без [query]) — краткосрочная
     *   память; уходит в запрос как список сообщений.
     * @param memory долговременная и рабочая память — встраивается в системный
     *   промпт. Владеет памятью вызывающая сторона, поэтому сам агент остаётся
     *   без скрытого состояния.
     */
    suspend fun ask(
        query: String,
        history: List<Message>,
        memory: MemoryContext = MemoryContext.EMPTY,
    ): AgentReply
}
