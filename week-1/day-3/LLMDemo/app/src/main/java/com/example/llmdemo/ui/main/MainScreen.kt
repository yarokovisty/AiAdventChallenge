package com.example.llmdemo.ui.main

import androidx.compose.foundation.clickable
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
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.navigation3.runtime.NavKey
import com.example.llmdemo.data.AnswerCheck
import com.example.llmdemo.data.ReasoningStep
import com.example.llmdemo.data.StrategyResult
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
                text = "Сравнение способов рассуждения",
                style = MaterialTheme.typography.titleMedium,
            )
            Spacer(Modifier.size(4.dp))
            Text(
                text = "Одна и та же задача решается 4 способами: прямой ответ · пошагово · самопромпт · группа экспертов.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Spacer(Modifier.size(12.dp))
            OutlinedTextField(
                value = state.problem,
                onValueChange = viewModel::onProblemChanged,
                modifier = Modifier.fillMaxWidth(),
                label = { Text("Задача") },
                enabled = !state.isRunning,
                maxLines = 5,
            )
            Spacer(Modifier.size(6.dp))
            Text(
                text = "Эталонный ответ: ${state.referenceAnswer}",
                style = MaterialTheme.typography.labelSmall,
                fontFamily = FontFamily.Monospace,
                color = MaterialTheme.colorScheme.primary,
            )
            Spacer(Modifier.size(8.dp))
            Row(verticalAlignment = Alignment.CenterVertically) {
                Button(
                    onClick = viewModel::runComparison,
                    enabled = state.problem.isNotBlank() && !state.isRunning,
                ) {
                    Text("Решить")
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
            Spacer(Modifier.size(8.dp))
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    text = "Статистика стабильности:",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                listOf(3, 5, 10).forEach { n ->
                    Spacer(Modifier.width(8.dp))
                    OutlinedButton(
                        onClick = { viewModel.runStability(n) },
                        enabled = state.problem.isNotBlank() && !state.isRunning,
                    ) {
                        Text("×$n")
                    }
                }
            }
        }

        HorizontalDivider()

        LazyColumn(
            modifier = Modifier.weight(1f),
            contentPadding = PaddingValues(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            if (state.stability.isNotEmpty()) {
                items(state.stability) { s ->
                    StabilityCard(s, state.referenceAnswer)
                }
            } else {
                items(state.results) { result ->
                    ResultCard(result, state.referenceAnswer)
                }
            }
        }
    }
}

@Composable
private fun ResultCard(result: StrategyResult, referenceAnswer: String) {
    val extracted = AnswerCheck.extractAnswer(result.finalAnswer)
    val isCorrect = !result.isError && extracted == referenceAnswer

    val container = when {
        result.isError -> MaterialTheme.colorScheme.errorContainer
        isCorrect -> MaterialTheme.colorScheme.tertiaryContainer
        else -> MaterialTheme.colorScheme.surfaceVariant
    }

    Card(
        modifier = Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = container),
    ) {
        Column(Modifier.padding(12.dp)) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    text = result.strategy.label,
                    style = MaterialTheme.typography.titleSmall,
                    fontWeight = FontWeight.Bold,
                )
                if (!result.isError) {
                    Text(
                        text = if (isCorrect) "✓ $extracted" else "✗ ${extracted ?: "—"} ≠ $referenceAnswer",
                        style = MaterialTheme.typography.labelMedium,
                        fontWeight = FontWeight.Bold,
                        color = if (isCorrect)
                            MaterialTheme.colorScheme.onTertiaryContainer
                        else
                            MaterialTheme.colorScheme.error,
                    )
                }
            }
            Spacer(Modifier.size(2.dp))
            Text(
                text = result.strategy.description,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Spacer(Modifier.size(8.dp))
            HorizontalDivider()
            Spacer(Modifier.size(8.dp))

            result.steps.forEach { step ->
                ExpandableStep(step)
                Spacer(Modifier.size(6.dp))
            }

            Text(
                text = "Финальный ответ",
                style = MaterialTheme.typography.labelMedium,
                fontWeight = FontWeight.Bold,
                color = MaterialTheme.colorScheme.primary,
            )
            Spacer(Modifier.size(2.dp))
            Text(
                text = result.finalAnswer,
                style = MaterialTheme.typography.bodyMedium,
            )

            if (!result.isError) {
                Spacer(Modifier.size(8.dp))
                Text(
                    text = "ответ: ${result.completionTokens} ток. · всего: ${result.totalTokens} ток.",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    }
}

@Composable
private fun StabilityCard(result: StabilityResult, referenceAnswer: String) {
    val allCorrect = result.correctCount == result.total
    val noneCorrect = result.correctCount == 0

    val container = when {
        allCorrect -> MaterialTheme.colorScheme.tertiaryContainer
        noneCorrect -> MaterialTheme.colorScheme.errorContainer
        else -> MaterialTheme.colorScheme.surfaceVariant
    }

    Card(
        modifier = Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = container),
    ) {
        Column(Modifier.padding(12.dp)) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    text = result.strategy.label,
                    style = MaterialTheme.typography.titleSmall,
                    fontWeight = FontWeight.Bold,
                )
                Text(
                    text = "${result.correctCount} / ${result.total} верно",
                    style = MaterialTheme.typography.labelMedium,
                    fontWeight = FontWeight.Bold,
                )
            }
            Spacer(Modifier.size(6.dp))
            Text(
                text = "Ответы по прогонам: ${result.answers.joinToString(", ")}",
                style = MaterialTheme.typography.bodyMedium,
                fontFamily = FontFamily.Monospace,
            )
            Spacer(Modifier.size(2.dp))
            Text(
                text = "эталон: $referenceAnswer",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

@Composable
private fun ExpandableStep(step: ReasoningStep) {
    var expanded by remember { mutableStateOf(false) }
    Column {
        Text(
            text = "${if (expanded) "▾" else "▸"} ${step.title}",
            style = MaterialTheme.typography.labelMedium,
            fontWeight = FontWeight.Bold,
            modifier = Modifier
                .fillMaxWidth()
                .clickable { expanded = !expanded },
        )
        if (expanded) {
            Spacer(Modifier.size(4.dp))
            Text(
                text = step.text,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}
