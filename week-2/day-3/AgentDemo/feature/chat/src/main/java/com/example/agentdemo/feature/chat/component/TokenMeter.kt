package com.example.agentdemo.feature.chat.component

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.FilterChip
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.example.agentdemo.feature.chat.ChatState
import kotlin.math.roundToInt

/**
 * Панель учёта токенов: прогресс-бар заполнения контекста, три счётчика
 * (запрос / история / ответ), накопленные токены и стоимость, а также сверка
 * локальной оценки с точным `usage`. Переключатель «демо-лимит» занижает потолок
 * контекста, чтобы переполнение достигалось за несколько сообщений.
 */
@Composable
fun TokenMeter(
    state: ChatState,
    onToggleDemoLimit: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val amber = Color(0xFFF59E0B)
    val barColor = when {
        state.willOverflow -> MaterialTheme.colorScheme.error
        state.contextUsedFraction > 0.8f -> amber
        else -> MaterialTheme.colorScheme.primary
    }
    val percent = (state.contextUsedFraction * 100).roundToInt()

    Surface(
        modifier = modifier.fillMaxWidth(),
        color = MaterialTheme.colorScheme.surface,
        tonalElevation = 2.dp,
    ) {
        Column(modifier = Modifier.padding(horizontal = 16.dp, vertical = 10.dp)) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    text = "Контекст · GLM-4.6",
                    style = MaterialTheme.typography.titleSmall,
                )
                FilterChip(
                    selected = state.demoLimit,
                    onClick = onToggleDemoLimit,
                    label = { Text("демо-лимит") },
                )
            }

            LinearProgressIndicator(
                progress = { state.contextUsedFraction },
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(top = 8.dp),
                color = barColor,
                trackColor = MaterialTheme.colorScheme.surfaceVariant,
            )
            Text(
                text = "${formatTokens(state.nextRequestTokens)} / ${formatTokens(state.limit)} ток.  ($percent%)",
                style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(top = 4.dp),
            )
            if (state.willOverflow) {
                Text(
                    text = "⚠ Переполнение: агент не сможет ответить — очистите историю",
                    style = MaterialTheme.typography.labelMedium,
                    fontWeight = FontWeight.Medium,
                    color = MaterialTheme.colorScheme.error,
                    modifier = Modifier.padding(top = 2.dp),
                )
            }

            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(top = 10.dp),
                horizontalArrangement = Arrangement.SpaceBetween,
            ) {
                Stat("Запрос", formatTokens(state.inputTokens), "оценка")
                Stat("История", formatTokens(state.contextTokens), "оценка")
                Stat(
                    label = "Ответ",
                    value = state.lastUsage?.let { formatTokens(it.completionTokens) } ?: "—",
                    hint = if (state.lastUsage != null) "факт" else "—",
                )
            }

            Text(
                text = "Σ за сессию: ${formatTokens(state.cumulativeTokens)} ток. · " +
                    formatUsd(state.cumulativeCostUsd),
                style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.onSurface,
                modifier = Modifier.padding(top = 10.dp),
            )

            val usage = state.lastUsage
            if (usage != null) {
                val error = state.estimateErrorPercent?.let { " · оценка ${formatErrorPercent(it)}" } ?: ""
                Text(
                    text = "последний usage: prompt ${formatTokens(usage.promptTokens)} · " +
                        "compl ${formatTokens(usage.completionTokens)} · " +
                        "total ${formatTokens(usage.totalTokens)}$error",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(top = 2.dp),
                )
            }
        }
    }
}

/** Одна колонка счётчика: подпись сверху, значение крупно, пометка «оценка/факт». */
@Composable
private fun Stat(label: String, value: String, hint: String) {
    Column(horizontalAlignment = Alignment.CenterHorizontally) {
        Text(
            text = label,
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Text(
            text = value,
            style = MaterialTheme.typography.titleMedium,
            fontWeight = FontWeight.SemiBold,
        )
        Text(
            text = hint,
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}
