package com.example.agentdemo.feature.chat.component

import java.util.Locale
import kotlin.math.abs

/** Число токенов с разбивкой по разрядам неразрывным пробелом: 1840 → "1 840". */
internal fun formatTokens(value: Int): String =
    value.toString()
        .reversed()
        .chunked(3)
        .joinToString(" ")
        .reversed()

/** Стоимость в USD: мелкие суммы показываем с достаточной точностью. */
internal fun formatUsd(value: Double): String = when {
    value == 0.0 -> "$0"
    value < 0.01 -> "$" + String.format(Locale.US, "%.4f", value)
    else -> "$" + String.format(Locale.US, "%.2f", value)
}

/** Погрешность оценки со знаком: 1.2 → "+1.2%", −3.4 → "−3.4%". */
internal fun formatErrorPercent(value: Double): String {
    val sign = if (value >= 0) "+" else "−"
    return sign + String.format(Locale.US, "%.1f", abs(value)) + "%"
}
