package com.example.agentdemo.feature.chat

import com.example.agentdemo.domain.model.Message
import com.example.agentdemo.domain.model.TokenUsage
import com.example.agentdemo.domain.token.AgentLimits

/**
 * Сообщение в ленте вместе с числом токенов.
 *
 * @param tokens токены сообщения: для реплик пользователя — локальная оценка,
 *   для ответов агента — факт из `usage`, если сервер его прислал.
 * @param exact `true`, если [tokens] взяты из серверного `usage`, а не из оценки.
 */
data class UiMessage(
    val message: Message,
    val tokens: Int,
    val exact: Boolean,
)

/** Иммутабельное состояние экрана чата (MVI: State). */
data class ChatState(
    val agentName: String = "",
    val agentRole: String = "",
    val input: String = "",
    val messages: List<UiMessage> = emptyList(),
    val isSending: Boolean = false,
    // --- учёт токенов ---
    /** Демо-режим: искусственно низкий лимит, чтобы показать переполнение. */
    val demoLimit: Boolean = false,
    /** Оценка: system-промпт + вся история (контекст, уже занятый до нового ввода). */
    val contextTokens: Int = 0,
    /** Оценка: весь следующий запрос = system + история + текущий ввод. */
    val nextRequestTokens: Int = 0,
    /** Факт последнего ответа модели (`usage`). */
    val lastUsage: TokenUsage? = null,
    /** Оценка prompt-токенов последнего отправленного запроса — для сверки с фактом. */
    val lastEstimatedPrompt: Int = 0,
    /** Факт: суммарно сожжённые токены за сессию (сумма `total_tokens`). */
    val cumulativeTokens: Int = 0,
    /** Факт: накопленная стоимость диалога, USD. */
    val cumulativeCostUsd: Double = 0.0,
) {
    /** Активный лимит контекста: реальный GLM-4.6 либо заниженный демо-лимит. */
    val limit: Int get() = if (demoLimit) AgentLimits.DEMO_LIMIT else AgentLimits.CONTEXT_WINDOW

    /** Оценка токенов только нового сообщения (дельта запроса над контекстом). */
    val inputTokens: Int get() = (nextRequestTokens - contextTokens).coerceAtLeast(0)

    /** Следующий запрос не влезет в контекст — агент «сломается» при отправке. */
    val willOverflow: Boolean get() = nextRequestTokens > limit

    /** Доля заполнения контекста [0..1] для прогресс-бара. */
    val contextUsedFraction: Float get() = (nextRequestTokens.toFloat() / limit).coerceIn(0f, 1f)

    // Кнопку «отправить» намеренно НЕ блокируем при переполнении: пусть пользователь
    // нажмёт и увидит, как агент отказывается продолжать (это и есть «что ломается»).
    val canSend: Boolean get() = input.isNotBlank() && !isSending

    /**
     * Погрешность локальной оценки prompt-токенов относительно факта, в процентах
     * (знак показывает, завысили (+) или занизили (−)). `null`, если факта ещё нет.
     */
    val estimateErrorPercent: Double?
        get() = lastUsage?.takeIf { it.promptTokens > 0 }?.let {
            (lastEstimatedPrompt - it.promptTokens) * 100.0 / it.promptTokens
        }
}

/** Намерения пользователя (MVI: Intent). Единственный способ изменить состояние. */
sealed interface ChatIntent {
    data class InputChanged(val text: String) : ChatIntent
    data object Send : ChatIntent
    data object Clear : ChatIntent
    data object ToggleDemoLimit : ChatIntent
}

/** Одноразовые события для UI (MVI: Effect). */
sealed interface ChatEffect {
    data class ShowError(val message: String) : ChatEffect
}
