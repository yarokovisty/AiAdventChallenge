package com.example.llmdemo.ui.main

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.navigation3.runtime.NavKey
import com.example.llmdemo.data.DefaultDataRepository

@Composable
fun MainScreen(
    onItemClick: (NavKey) -> Unit,
    modifier: Modifier = Modifier,
    viewModel: MainScreenViewModel = viewModel { MainScreenViewModel(DefaultDataRepository()) },
) {
    val state by viewModel.uiState.collectAsStateWithLifecycle()

    Column(modifier.fillMaxSize().imePadding()) {
        Column(Modifier.padding(16.dp)) {
            Text(
                text = "Сравнение моделей на промпте-ловушке",
                style = MaterialTheme.typography.titleMedium,
            )
            Spacer(Modifier.size(4.dp))
            Text(
                text = "Один и тот же запрос уходит на 4 модели GLM разного уровня. " +
                    "Замеряем время, токены и стоимость, сравниваем качество/скорость/ресурсы.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Spacer(Modifier.size(12.dp))
            OutlinedTextField(
                value = state.prompt,
                onValueChange = viewModel::onPromptChanged,
                modifier = Modifier.fillMaxWidth(),
                label = { Text("Промпт-ловушка") },
                enabled = !state.isRunning,
                maxLines = 5,
            )
            Spacer(Modifier.size(8.dp))
            Row(verticalAlignment = Alignment.CenterVertically) {
                Button(
                    onClick = viewModel::runComparison,
                    enabled = state.prompt.isNotBlank() && !state.isRunning,
                ) {
                    Text("Сравнить")
                }
                if (state.isRunning) {
                    Spacer(Modifier.width(12.dp))
                    CircularProgressIndicator(modifier = Modifier.size(20.dp))
                    Spacer(Modifier.width(8.dp))
                    Text(
                        text = state.runningLabel ?: "...",
                        style = MaterialTheme.typography.bodySmall,
                    )
                }
            }
        }

        HorizontalDivider()

        LazyColumn(
            modifier = Modifier.weight(1f),
            contentPadding = PaddingValues(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            if (state.results.size > 1 && !state.isRunning) {
                item { SummaryCard(state.results) }
            }
            items(state.results) { result ->
                ModelCard(result)
            }
        }
    }
}

/** Короткая сводка: кто решил ловушку, самый быстрый и самый дешёвый. */
@Composable
private fun SummaryCard(results: List<ModelResult>) {
    val ok = results.filterNot { it.isError }
    val fastest = ok.minByOrNull { it.latencyMs }
    val cheapest = ok.minByOrNull { it.costUsd }
    val solved = ok.filter { it.correctness == true }.map { it.config.label }

    Card(
        modifier = Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.primaryContainer,
        ),
    ) {
        Column(Modifier.padding(12.dp)) {
            Text(
                text = "Итог сравнения",
                style = MaterialTheme.typography.titleSmall,
                fontWeight = FontWeight.Bold,
            )
            Spacer(Modifier.size(4.dp))
            Text(
                text = "Решили ловушку (5¢): " +
                    if (solved.isEmpty()) "никто" else solved.joinToString(", "),
                style = MaterialTheme.typography.bodySmall,
            )
            fastest?.let {
                Text(
                    text = "Быстрее всех: ${it.config.label} (${fmtSeconds(it.latencyMs)})",
                    style = MaterialTheme.typography.bodySmall,
                )
            }
            cheapest?.let {
                Text(
                    text = "Дешевле всех: ${it.config.label} (${fmtCost(it.costUsd)})",
                    style = MaterialTheme.typography.bodySmall,
                )
            }
        }
    }
}

@Composable
private fun ModelCard(result: ModelResult) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(
            containerColor = if (result.isError)
                MaterialTheme.colorScheme.errorContainer
            else
                MaterialTheme.colorScheme.surfaceVariant,
        ),
    ) {
        Column(Modifier.padding(12.dp)) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    text = result.config.label,
                    style = MaterialTheme.typography.titleSmall,
                    fontWeight = FontWeight.Bold,
                )
                Text(
                    text = correctnessBadge(result.correctness),
                    style = MaterialTheme.typography.titleSmall,
                )
            }
            Text(
                text = result.config.level,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Spacer(Modifier.size(2.dp))
            Text(
                text = result.config.priceSummary,
                style = MaterialTheme.typography.labelSmall,
                fontFamily = FontFamily.Monospace,
                color = MaterialTheme.colorScheme.primary,
            )

            if (!result.isError) {
                Spacer(Modifier.size(8.dp))
                Text(
                    text = "⏱ ${fmtSeconds(result.latencyMs)}   " +
                        "🔢 ${result.promptTokens}+${result.completionTokens}=${result.totalTokens} ток.",
                    style = MaterialTheme.typography.labelMedium,
                    fontWeight = FontWeight.Bold,
                )
                Text(
                    text = "💲 ${fmtCost(result.costUsd)} / запрос   ·   " +
                        "≈ ${fmtCost(result.costPer1000Usd)} / 1000 запросов",
                    style = MaterialTheme.typography.labelMedium,
                    fontWeight = FontWeight.Bold,
                    color = MaterialTheme.colorScheme.secondary,
                )
            }

            Spacer(Modifier.size(8.dp))
            HorizontalDivider()
            Spacer(Modifier.size(8.dp))
            Text(
                text = result.content,
                style = MaterialTheme.typography.bodyMedium,
            )
        }
    }
}

private fun correctnessBadge(correctness: Boolean?): String = when (correctness) {
    true -> "✓ верно"
    false -> "✗ ловушка"
    null -> "? неясно"
}

private fun fmtSeconds(ms: Long): String = "%.1f с".format(ms / 1000.0)

/** Крошечные суммы показываем с достаточной точностью; бесплатно — отдельной подписью. */
private fun fmtCost(usd: Double): String =
    if (usd == 0.0) "бесплатно" else "$" + "%.6f".format(usd)
