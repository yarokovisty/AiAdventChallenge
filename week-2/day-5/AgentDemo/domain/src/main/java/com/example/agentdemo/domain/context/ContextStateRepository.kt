package com.example.agentdemo.domain.context

/**
 * Порт хранилища состояния управления контекстом (стратегия + факты + ветки).
 *
 * По тому же принципу, что и [com.example.agentdemo.domain.conversation.ConversationRepository]:
 * домен объявляет контракт, реализацию (JSON-файл) даёт слой data. Благодаря
 * этому выбранная стратегия и её данные переживают перезапуск приложения.
 */
interface ContextStateRepository {

    /** Загрузить сохранённое состояние ([ContextState.DEFAULT], если его ещё нет). */
    suspend fun load(): ContextState

    /** Полностью перезаписать сохранённое состояние. */
    suspend fun save(state: ContextState)

    /** Стереть сохранённое состояние. */
    suspend fun clear()
}
