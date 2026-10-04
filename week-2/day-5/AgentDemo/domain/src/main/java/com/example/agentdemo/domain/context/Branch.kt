package com.example.agentdemo.domain.context

import com.example.agentdemo.domain.model.Message

/** Одна ветка диалога: именованная самостоятельная история сообщений. */
data class Branch(
    val id: String,
    val name: String,
    val messages: List<Message>,
)

/**
 * Состояние ветвления диалога для стратегии [ContextStrategy.BRANCHING].
 *
 * Пользователь ставит [checkpointIndex] (чекпоинт) на текущей точке диалога, затем
 * форкает её на две ветки от одного места и продолжает каждую независимо,
 * переключаясь между ними по [activeId].
 *
 * @param branches все ветки, кроме активной (их снапшоты лежат «на полке»);
 *   сообщения активной ветки живут в основной ленте диалога.
 * @param activeId идентификатор активной ветки (null — ветвление ещё не создано).
 * @param checkpointIndex длина ленты в момент чекпоинта (точка форка), либо null.
 */
data class BranchingState(
    val branches: List<Branch> = emptyList(),
    val activeId: String? = null,
    val checkpointIndex: Int? = null,
) {
    val isForked: Boolean get() = branches.isNotEmpty() && activeId != null

    companion object {
        val EMPTY = BranchingState()
    }
}
