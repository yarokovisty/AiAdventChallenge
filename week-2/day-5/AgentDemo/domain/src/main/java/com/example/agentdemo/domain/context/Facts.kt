package com.example.agentdemo.domain.context

/** Один извлечённый из диалога факт: пара ключ→значение (цель, ограничение, предпочтение…). */
data class Fact(
    val key: String,
    val value: String,
)

/**
 * Блок «липких фактов» — ключевые данные диалога, которые стратегия
 * [ContextStrategy.STICKY_FACTS] держит в контексте независимо от того, как
 * далеко уехало окно последних сообщений.
 */
data class Facts(
    val items: List<Fact> = emptyList(),
) {
    val isEmpty: Boolean get() = items.isEmpty()

    /** Готовый к вставке в запрос блок фактов (пустая строка, если фактов нет). */
    fun render(): String {
        if (items.isEmpty()) return ""
        return buildString {
            appendLine("Важные факты из диалога (учитывай их при ответе):")
            items.forEach { appendLine("- ${it.key}: ${it.value}") }
        }.trim()
    }

    companion object {
        val EMPTY = Facts()
    }
}
