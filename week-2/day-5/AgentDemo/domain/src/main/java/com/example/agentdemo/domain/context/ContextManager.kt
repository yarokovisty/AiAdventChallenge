package com.example.agentdemo.domain.context

import com.example.agentdemo.domain.model.Message

/**
 * Доменный сервис, который по выбранной [ContextStrategy] решает, **что** из
 * накопленной истории уйдёт в модель. Это ядро задания — здесь живут все три
 * стратегии управления контекстом, а переключается между ними вызывающая сторона.
 *
 * Сервис чистый (без сети и ввода-вывода): извлечение фактов и обращение к модели
 * делают другие компоненты, а [ContextManager] лишь нарезает историю и
 * подставляет факты.
 *
 * @param windowSize размер окна последних реплик (N) для стратегий
 *   [ContextStrategy.SLIDING_WINDOW] и [ContextStrategy.STICKY_FACTS].
 */
class ContextManager(
    val windowSize: Int = DEFAULT_WINDOW,
) {

    /**
     * Подготовленный к отправке контекст.
     *
     * @param history срез истории, который уйдёт в модель как есть.
     * @param facts блок фактов отдельным system-сообщением (null — фактов нет).
     */
    data class PreparedContext(
        val history: List<Message>,
        val facts: String?,
    )

    /**
     * Собирает контекст для стратегии [strategy] из полной [history] (сообщения
     * ДО нового запроса) и актуальных [facts].
     *
     * - [ContextStrategy.SLIDING_WINDOW] — только последние [windowSize] реплик;
     * - [ContextStrategy.STICKY_FACTS] — блок фактов + последние [windowSize] реплик;
     * - [ContextStrategy.BRANCHING] — вся история активной ветки целиком.
     */
    fun prepare(
        strategy: ContextStrategy,
        history: List<Message>,
        facts: Facts,
    ): PreparedContext = when (strategy) {
        ContextStrategy.SLIDING_WINDOW -> PreparedContext(
            history = history.takeLast(windowSize),
            facts = null,
        )

        ContextStrategy.STICKY_FACTS -> PreparedContext(
            history = history.takeLast(windowSize),
            facts = facts.render().ifBlank { null },
        )

        ContextStrategy.BRANCHING -> PreparedContext(
            history = history,
            facts = null,
        )
    }

    companion object {
        const val DEFAULT_WINDOW = 6
    }
}
