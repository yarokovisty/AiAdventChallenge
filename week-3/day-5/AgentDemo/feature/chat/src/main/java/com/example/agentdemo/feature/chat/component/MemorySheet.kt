package com.example.agentdemo.feature.chat.component

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Close
import androidx.compose.material3.Button
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.example.agentdemo.domain.memory.KnowledgeKind
import com.example.agentdemo.domain.memory.LongTermFact
import com.example.agentdemo.domain.memory.WorkingMemory
import com.example.agentdemo.feature.chat.ChatIntent
import com.example.agentdemo.feature.chat.ChatState

/**
 * Панель памяти ассистента — три явно разделённых слоя.
 *
 * Здесь пользователь сам решает, что и в какой слой сохранить: добавить факт в
 * долговременную память (с выбором вида), задать задачу/заметку в рабочую память
 * или очистить диалог (краткосрочную). Так модель памяти становится видимой и
 * управляемой, а не скрытой внутри агента.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun MemorySheet(
    state: ChatState,
    onIntent: (ChatIntent) -> Unit,
) {
    val sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)

    ModalBottomSheet(
        onDismissRequest = { onIntent(ChatIntent.CloseMemory) },
        sheetState = sheetState,
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 20.dp)
                .padding(bottom = 32.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Text(
                text = "Память ассистента",
                style = MaterialTheme.typography.titleLarge,
            )
            Text(
                text = "Три слоя хранятся отдельно. Выберите, что и куда сохранить — " +
                    "это и влияет на ответы агента.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )

            LongTermSection(facts = state.longTerm, onIntent = onIntent)
            HorizontalDivider(modifier = Modifier.padding(vertical = 8.dp))
            WorkingSection(working = state.working, onIntent = onIntent)
            HorizontalDivider(modifier = Modifier.padding(vertical = 8.dp))
            ShortTermSection(messageCount = state.messages.size, onIntent = onIntent)
        }
    }
}

/* ----------------------------- Долговременная ----------------------------- */

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun LongTermSection(
    facts: List<LongTermFact>,
    onIntent: (ChatIntent) -> Unit,
) {
    var text by remember { mutableStateOf("") }
    var kind by remember { mutableStateOf(KnowledgeKind.PROFILE) }

    SectionHeader(
        title = "Долговременная память",
        subtitle = "Профиль, решения, знания. Всегда в контексте, переживает очистку диалога.",
    )

    if (facts.isEmpty()) {
        EmptyHint("Пока пусто")
    } else {
        facts.forEach { fact ->
            Row(
                modifier = Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    text = "${fact.kind.label}: ${fact.text}",
                    style = MaterialTheme.typography.bodyMedium,
                    modifier = Modifier.weight(1f),
                )
                IconButton(onClick = { onIntent(ChatIntent.RemoveLongTermFact(fact.id)) }) {
                    Icon(Icons.Outlined.Close, contentDescription = "Удалить факт")
                }
            }
        }
    }

    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        KnowledgeKind.entries.forEach { option ->
            FilterChip(
                selected = kind == option,
                onClick = { kind = option },
                label = { Text(option.label) },
            )
        }
    }

    OutlinedTextField(
        value = text,
        onValueChange = { text = it },
        modifier = Modifier.fillMaxWidth(),
        label = { Text("Что запомнить надолго") },
        singleLine = false,
    )

    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Button(
            onClick = {
                onIntent(ChatIntent.AddLongTermFact(kind, text))
                text = ""
            },
            enabled = text.isNotBlank(),
        ) { Text("Сохранить факт") }

        if (facts.isNotEmpty()) {
            TextButton(onClick = { onIntent(ChatIntent.ClearLongTerm) }) {
                Text("Очистить слой")
            }
        }
    }
}

/* ------------------------------- Рабочая --------------------------------- */

@Composable
private fun WorkingSection(
    working: WorkingMemory?,
    onIntent: (ChatIntent) -> Unit,
) {
    SectionHeader(
        title = "Рабочая память",
        subtitle = "Текущая задача и заметки по ней. В контексте, пока задача не завершена.",
    )

    if (working == null) {
        var task by remember { mutableStateOf("") }
        OutlinedTextField(
            value = task,
            onValueChange = { task = it },
            modifier = Modifier.fillMaxWidth(),
            label = { Text("Сформулируйте текущую задачу") },
            singleLine = true,
        )
        Button(
            onClick = {
                onIntent(ChatIntent.SetTask(task))
                task = ""
            },
            enabled = task.isNotBlank(),
        ) { Text("Начать задачу") }
    } else {
        Text(
            text = "Задача: ${working.task}",
            style = MaterialTheme.typography.bodyMedium,
            fontWeight = FontWeight.SemiBold,
        )
        working.notes.forEach { note ->
            Text("• $note", style = MaterialTheme.typography.bodyMedium)
        }

        var note by remember { mutableStateOf("") }
        OutlinedTextField(
            value = note,
            onValueChange = { note = it },
            modifier = Modifier.fillMaxWidth(),
            label = { Text("Добавить заметку по задаче") },
            singleLine = true,
        )
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Button(
                onClick = {
                    onIntent(ChatIntent.AddWorkingNote(note))
                    note = ""
                },
                enabled = note.isNotBlank(),
            ) { Text("Добавить заметку") }
            TextButton(onClick = { onIntent(ChatIntent.FinishTask) }) {
                Text("Завершить задачу")
            }
        }
    }
}

/* ----------------------------- Краткосрочная ----------------------------- */

@Composable
private fun ShortTermSection(
    messageCount: Int,
    onIntent: (ChatIntent) -> Unit,
) {
    SectionHeader(
        title = "Краткосрочная память",
        subtitle = "История текущего диалога. Уходит в запрос как список сообщений.",
    )
    Text(
        text = "Сообщений в диалоге: $messageCount",
        style = MaterialTheme.typography.bodyMedium,
    )
    if (messageCount > 0) {
        TextButton(onClick = { onIntent(ChatIntent.Clear) }) {
            Text("Очистить диалог")
        }
    }
}

/* ------------------------------- Общее ----------------------------------- */

@Composable
private fun SectionHeader(title: String, subtitle: String) {
    Text(
        text = title,
        style = MaterialTheme.typography.titleMedium,
        modifier = Modifier.padding(top = 4.dp),
    )
    Text(
        text = subtitle,
        style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )
}

@Composable
private fun EmptyHint(text: String) {
    Text(
        text = text,
        style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )
}

/** Человекочитаемая подпись вида факта. */
private val KnowledgeKind.label: String
    get() = when (this) {
        KnowledgeKind.PROFILE -> "Профиль"
        KnowledgeKind.DECISION -> "Решение"
        KnowledgeKind.KNOWLEDGE -> "Знание"
    }
