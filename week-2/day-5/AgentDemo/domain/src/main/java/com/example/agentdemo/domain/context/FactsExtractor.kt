package com.example.agentdemo.domain.context

import com.example.agentdemo.domain.model.Message

/**
 * Порт извлечения «липких фактов» из диалога (принцип инверсии зависимостей).
 *
 * Домен объявляет контракт, а реализацию (отдельное обращение к LLM) поставляет
 * слой data — тот же приём, что с [com.example.agentdemo.domain.llm.LlmClient].
 */
interface FactsExtractor {

    /**
     * Обновляет блок фактов после нового сообщения пользователя: дополняет
     * [previous] данными из [userMessage] (с учётом [recentContext]) и возвращает
     * обновлённый набор фактов.
     *
     * @param previous уже накопленные факты.
     * @param userMessage новое сообщение пользователя.
     * @param recentContext несколько последних реплик — для разрешения ссылок
     *   вроде «да, подходит», «пусть будет второй вариант».
     */
    suspend fun update(
        previous: Facts,
        userMessage: String,
        recentContext: List<Message>,
    ): Facts
}
