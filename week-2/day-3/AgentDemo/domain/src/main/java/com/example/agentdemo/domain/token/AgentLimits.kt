package com.example.agentdemo.domain.token

import com.example.agentdemo.domain.model.TokenUsage

/**
 * Лимиты контекста и цены токенов для модели агента (GLM-4.6 на Z.ai).
 *
 * Здесь же — расчёт стоимости запроса: именно он показывает, как по мере роста
 * диалога растёт не только число токенов, но и деньги (вся история каждый раз
 * уходит заново во входных токенах).
 */
object AgentLimits {

    /** Реальное контекстное окно GLM-4.6: вход + выход вместе, 200K токенов. */
    const val CONTEXT_WINDOW = 200_000

    /**
     * Искусственно низкий лимит для демонстрации переполнения: набрать 200K
     * вручную нереально, поэтому в демо-режиме «потолок» занижаем, чтобы
     * переполнение достигалось за несколько сообщений.
     */
    const val DEMO_LIMIT = 2_000

    // Цены Z.ai для GLM-4.6, USD за один токен (в прайсе — за 1M).
    private const val PRICE_INPUT = 0.60 / 1_000_000
    private const val PRICE_OUTPUT = 2.20 / 1_000_000
    private const val PRICE_CACHED_INPUT = 0.11 / 1_000_000

    /** Стоимость одного запроса в USD по его точному расходу токенов. */
    fun costUsd(usage: TokenUsage): Double {
        val freshInput = (usage.promptTokens - usage.cachedTokens).coerceAtLeast(0)
        return freshInput * PRICE_INPUT +
            usage.cachedTokens * PRICE_CACHED_INPUT +
            usage.completionTokens * PRICE_OUTPUT
    }
}
