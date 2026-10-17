package com.example.agentdemo.domain.conversation

import com.example.agentdemo.domain.model.Message

/**
 * Порт хранилища истории диалога (persistence) для домена.
 *
 * Домену неважно, где физически лежит история — файл, БД или сеть: он объявляет
 * только контракт, а конкретную реализацию поставляет слой data (принцип инверсии
 * зависимостей, как и с [com.example.agentdemo.domain.llm.LlmClient]).
 *
 * Благодаря этому порту агент остаётся без скрытого состояния: контекст беседы
 * переживает перезапуск приложения, но хранится отдельно от самого агента.
 */
interface ConversationRepository {

    /** Загрузить сохранённую историю (пустой список, если её ещё нет). */
    suspend fun load(): List<Message>

    /** Полностью перезаписать сохранённую историю текущим списком сообщений. */
    suspend fun save(messages: List<Message>)

    /** Стереть сохранённую историю. */
    suspend fun clear()
}
