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
import androidx.compose.material3.Surface
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
                ActiveTask(
                    task = task,
                    rejection = state.lastTaskRejection,
                    onIntent = onIntent,
                )
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
    rejection: String?,
    onIntent: (ChatIntent) -> Unit,
) {
    StageStepper(current = task.stage, isPaused = task.isPaused, onIntent = onIntent)
    Text(
        text = "Нажми на этап, чтобы попробовать перейти в него: недопустимый переход " +
            "(перепрыгивание этапа или закрытый шлюз) будет отклонён с объяснением.",
        style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )

    // Видимая «реакция» автомата на последний отклонённый переход.
    if (rejection != null) {
        RejectionBanner(reason = rejection)
    }

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

    // Статус шлюзов: пока шлюз закрыт, соответствующий переход автомат отклоняет.
    FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        GateChip(title = "План утверждён", isOpen = task.planApproved)
        GateChip(title = "Валидация пройдена", isOpen = task.validationPassed)
    }

    // Открытие шлюзов — доступно только на «своём» этапе.
    if (task.stage == TaskStage.PLANNING && !task.planApproved && !task.isPaused) {
        Button(onClick = { onIntent(ChatIntent.ApprovePlan) }) {
            Text("✓ Утвердить план")
        }
    }
    if (task.stage == TaskStage.VALIDATION && !task.validationPassed && !task.isPaused) {
        Button(onClick = { onIntent(ChatIntent.MarkValidationPassed) }) {
            Text("✓ Валидация пройдена")
        }
    }

    // Управление автоматом. На паузе переходы запрещены — сначала «Продолжить».
    // «Далее» всегда проходит через шлюзы: если шлюз закрыт, автомат отклонит
    // переход и покажет причину (а не выполнит его молча).
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

/**
 * Индикатор прогресса: все этапы, текущий выделен. Чипы нажимаемы (кроме текущего)
 * — тап отправляет [ChatIntent.RequestStage], то есть попытку перейти в этот этап.
 * Недопустимый переход автомат отклонит, причина придёт в снекбар — так видно,
 * что «перепрыгнуть» этап нельзя.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun StageStepper(
    current: TaskStage,
    isPaused: Boolean,
    onIntent: (ChatIntent) -> Unit,
) {
    FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        TaskStage.entries.forEach { stage ->
            val isCurrent = stage == current
            val done = stage.order < current.order
            AssistChip(
                onClick = { onIntent(ChatIntent.RequestStage(stage)) },
                enabled = !isCurrent,
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
                    disabledContainerColor = MaterialTheme.colorScheme.primaryContainer,
                    disabledLabelColor = MaterialTheme.colorScheme.onPrimaryContainer,
                    labelColor = MaterialTheme.colorScheme.onSurfaceVariant,
                ),
            )
        }
    }
}

/** Карточка с причиной последнего отклонённого перехода — «реакция» автомата. */
@Composable
private fun RejectionBanner(reason: String) {
    Surface(
        modifier = Modifier.fillMaxWidth(),
        color = MaterialTheme.colorScheme.errorContainer,
        shape = MaterialTheme.shapes.medium,
    ) {
        Column(modifier = Modifier.padding(12.dp)) {
            Text(
                text = "⛔ Переход отклонён",
                style = MaterialTheme.typography.titleSmall,
                color = MaterialTheme.colorScheme.onErrorContainer,
                fontWeight = FontWeight.SemiBold,
            )
            Text(
                text = reason,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onErrorContainer,
            )
        }
    }
}

/** Бейдж статуса шлюза: открыт (зелёная галочка) или закрыт (замок). */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun GateChip(title: String, isOpen: Boolean) {
    SuggestionChip(
        onClick = {},
        label = { Text(if (isOpen) "✓ $title" else "🔒 $title") },
        colors = SuggestionChipDefaults.suggestionChipColors(
            containerColor = if (isOpen) {
                MaterialTheme.colorScheme.secondaryContainer
            } else {
                Color.Transparent
            },
        ),
    )
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
