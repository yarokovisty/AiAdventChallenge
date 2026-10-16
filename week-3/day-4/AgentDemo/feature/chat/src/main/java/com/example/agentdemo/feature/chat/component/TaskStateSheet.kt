package com.example.agentdemo.feature.chat.component

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AssistChip
import androidx.compose.material3.Button
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.SuggestionChip
import androidx.compose.material3.SuggestionChipDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.example.agentdemo.domain.task.TaskStage
import com.example.agentdemo.domain.task.TaskState
import com.example.agentdemo.domain.task.TaskStateMachine
import com.example.agentdemo.feature.chat.ChatIntent
import com.example.agentdemo.feature.chat.ChatState

/**
 * Панель состояния задачи — визуализация конечного автомата.
 *
 * Показывает три формализованных поля (этап → шаг → ожидаемое действие),
 * индикатор прогресса по этапам и управление переходами: «Далее», «На доработку»,
 * «Пауза»/«Продолжить», «Сброс». Пауза возможна на любом этапе; после неё и даже
 * после перезапуска приложения задача продолжается с того же шага, потому что
 * состояние автомата сохранено и уходит в системный промпт каждого запроса.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun TaskStateSheet(
    state: ChatState,
    onIntent: (ChatIntent) -> Unit,
) {
    val sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)

    ModalBottomSheet(
        onDismissRequest = { onIntent(ChatIntent.CloseTask) },
        sheetState = sheetState,
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 20.dp)
                .padding(bottom = 32.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Text(
                text = "Состояние задачи",
                style = MaterialTheme.typography.titleLarge,
            )
            Text(
                text = "Задача — конечный автомат: этап → шаг → ожидаемое действие. " +
                    "Состояние сохраняется и уходит в каждый запрос, поэтому задачу можно " +
                    "поставить на паузу и продолжить без повторных объяснений.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )

            val task = state.task
            if (task == null) {
                NewTaskForm(onIntent = onIntent)
            } else {
                ActiveTask(task = task, onIntent = onIntent)
            }
        }
    }
}

@Composable
private fun NewTaskForm(onIntent: (ChatIntent) -> Unit) {
    var goal by remember { mutableStateOf("") }
    OutlinedTextField(
        value = goal,
        onValueChange = { goal = it },
        modifier = Modifier.fillMaxWidth(),
        label = { Text("Цель задачи") },
        singleLine = false,
    )
    Button(
        onClick = {
            onIntent(ChatIntent.StartTask(goal))
            goal = ""
        },
        enabled = goal.isNotBlank(),
    ) { Text("Начать задачу") }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun ActiveTask(
    task: TaskState,
    onIntent: (ChatIntent) -> Unit,
) {
    StageStepper(current = task.stage, isPaused = task.isPaused)

    Text(
        text = "Цель: ${task.goal}",
        style = MaterialTheme.typography.bodyMedium,
        fontWeight = FontWeight.SemiBold,
    )

    if (task.isPaused) {
        SuggestionChip(
            onClick = {},
            label = { Text("⏸ На паузе — продолжится с того же шага") },
            colors = SuggestionChipDefaults.suggestionChipColors(
                containerColor = MaterialTheme.colorScheme.secondaryContainer,
            ),
        )
    }

    Field(title = "Этап", value = "${task.stage.title} (${task.stage.slug})")
    Field(title = "Текущий шаг", value = task.currentStep)
    Field(title = "Ожидаемое действие", value = task.expectedAction)

    // Управление автоматом. На паузе переходы запрещены — сначала «Продолжить».
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        if (TaskStateMachine.canAdvance(task)) {
            Button(onClick = { onIntent(ChatIntent.AdvanceStage) }) {
                Text(if (task.stage == TaskStage.VALIDATION) "Завершить" else "Далее →")
            }
        }
        if (TaskStateMachine.canRollback(task)) {
            OutlinedButton(onClick = { onIntent(ChatIntent.RollbackStage) }) {
                Text("На доработку")
            }
        }
    }

    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        if (task.isPaused) {
            FilledTonalButton(onClick = { onIntent(ChatIntent.ResumeTask) }) {
                Text("▶ Продолжить")
            }
        } else if (!task.isDone) {
            FilledTonalButton(onClick = { onIntent(ChatIntent.PauseTask) }) {
                Text("⏸ Пауза")
            }
        }
        TextButton(onClick = { onIntent(ChatIntent.ResetTask) }) {
            Text("Сбросить задачу")
        }
    }

    HorizontalDivider(modifier = Modifier.padding(vertical = 4.dp))
    StepEditor(task = task, onIntent = onIntent)

    if (task.log.isNotEmpty()) {
        HorizontalDivider(modifier = Modifier.padding(vertical = 4.dp))
        Text("Журнал задачи", style = MaterialTheme.typography.titleSmall)
        task.log.forEach { entry ->
            Text("• $entry", style = MaterialTheme.typography.bodySmall)
        }
    }
}

/** Индикатор прогресса: все этапы, текущий выделен. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun StageStepper(current: TaskStage, isPaused: Boolean) {
    FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        TaskStage.entries.forEach { stage ->
            val isCurrent = stage == current
            val done = stage.order < current.order
            AssistChip(
                onClick = {},
                enabled = false,
                label = {
                    val mark = when {
                        done -> "✓ "
                        isCurrent && isPaused -> "⏸ "
                        isCurrent -> "● "
                        else -> ""
                    }
                    Text("$mark${stage.title}")
                },
                colors = androidx.compose.material3.AssistChipDefaults.assistChipColors(
                    disabledContainerColor = if (isCurrent) {
                        MaterialTheme.colorScheme.primaryContainer
                    } else {
                        Color.Transparent
                    },
                    disabledLabelColor = if (isCurrent) {
                        MaterialTheme.colorScheme.onPrimaryContainer
                    } else {
                        MaterialTheme.colorScheme.onSurfaceVariant
                    },
                ),
            )
        }
    }
}

/** Редактор текущего шага и ожидаемого действия (не меняет этап). */
@Composable
private fun StepEditor(
    task: TaskState,
    onIntent: (ChatIntent) -> Unit,
) {
    var step by remember(task.stage, task.currentStep) { mutableStateOf(task.currentStep) }
    var action by remember(task.stage, task.expectedAction) { mutableStateOf(task.expectedAction) }

    Text("Уточнить шаг", style = MaterialTheme.typography.titleSmall)
    OutlinedTextField(
        value = step,
        onValueChange = { step = it },
        modifier = Modifier.fillMaxWidth(),
        label = { Text("Текущий шаг") },
        singleLine = false,
    )
    OutlinedTextField(
        value = action,
        onValueChange = { action = it },
        modifier = Modifier.fillMaxWidth(),
        label = { Text("Ожидаемое действие") },
        singleLine = false,
    )
    Button(
        onClick = { onIntent(ChatIntent.UpdateStep(step, action)) },
        enabled = step.isNotBlank() &&
            (step != task.currentStep || action != task.expectedAction),
    ) { Text("Сохранить шаг") }
}

@Composable
private fun Field(title: String, value: String) {
    Column {
        Text(
            text = title,
            style = MaterialTheme.typography.labelMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Text(text = value, style = MaterialTheme.typography.bodyMedium)
    }
}
