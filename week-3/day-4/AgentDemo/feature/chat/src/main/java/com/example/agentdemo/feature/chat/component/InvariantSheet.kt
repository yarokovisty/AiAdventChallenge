package com.example.agentdemo.feature.chat.component

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Switch
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
import com.example.agentdemo.domain.invariant.Invariant
import com.example.agentdemo.domain.invariant.InvariantCategory
import com.example.agentdemo.feature.chat.ChatIntent
import com.example.agentdemo.feature.chat.ChatState

/**
 * Панель инвариантов — жёстких правил, которые ассистент не вправе нарушить.
 *
 * Показывает правила, сгруппированные по категориям (архитектура / техрешение /
 * стек / бизнес-правило), позволяет временно снять правило (переключатель) или
 * удалить его, а также добавить новое. Инварианты хранятся отдельным слоем и
 * уходят в системный промпт каждого запроса: активные правила агент обязан
 * учитывать и отказываться от решений, которые их нарушают.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun InvariantSheet(
    state: ChatState,
    onIntent: (ChatIntent) -> Unit,
) {
    val sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)

    ModalBottomSheet(
        onDismissRequest = { onIntent(ChatIntent.CloseInvariants) },
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
                text = "Инварианты проекта",
                style = MaterialTheme.typography.titleLarge,
            )
            Text(
                text = "Жёсткие правила, которые ассистент не вправе нарушить. Они уходят в " +
                    "каждый запрос самым приоритетным блоком. Если запрос требует нарушить " +
                    "инвариант — ассистент откажется и объяснит причину.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Text(
                text = "Активно: ${state.activeInvariantCount} из ${state.invariants.size}",
                style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )

            // Инварианты по категориям — в том же порядке, что и в системном промпте.
            InvariantCategory.entries.forEach { category ->
                val group = state.invariants.filter { it.category == category }
                if (group.isEmpty()) return@forEach
                Text(
                    text = category.title,
                    style = MaterialTheme.typography.titleSmall,
                    color = MaterialTheme.colorScheme.primary,
                )
                group.forEach { invariant ->
                    InvariantRow(invariant = invariant, onIntent = onIntent)
                }
            }

            HorizontalDivider(modifier = Modifier.padding(vertical = 4.dp))
            NewInvariantForm(onIntent = onIntent)
        }
    }
}

@Composable
private fun InvariantRow(
    invariant: Invariant,
    onIntent: (ChatIntent) -> Unit,
) {
    Column {
        Row(
            modifier = Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Switch(
                checked = invariant.isActive,
                onCheckedChange = {
                    onIntent(ChatIntent.ToggleInvariant(invariant.id, it))
                },
            )
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text = invariant.rule,
                    style = MaterialTheme.typography.bodyMedium,
                    fontWeight = FontWeight.Medium,
                    color = if (invariant.isActive) {
                        MaterialTheme.colorScheme.onSurface
                    } else {
                        MaterialTheme.colorScheme.onSurfaceVariant
                    },
                )
                if (invariant.rationale.isNotBlank()) {
                    Text(
                        text = "Почему: ${invariant.rationale}",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
            TextButton(onClick = { onIntent(ChatIntent.RemoveInvariant(invariant.id)) }) {
                Text("Удалить")
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun NewInvariantForm(onIntent: (ChatIntent) -> Unit) {
    var category by remember { mutableStateOf(InvariantCategory.ARCHITECTURE) }
    var rule by remember { mutableStateOf("") }
    var rationale by remember { mutableStateOf("") }

    Text("Добавить инвариант", style = MaterialTheme.typography.titleSmall)

    FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        InvariantCategory.entries.forEach { option ->
            FilterChip(
                selected = category == option,
                onClick = { category = option },
                label = { Text(option.title) },
            )
        }
    }

    OutlinedTextField(
        value = rule,
        onValueChange = { rule = it },
        modifier = Modifier.fillMaxWidth(),
        label = { Text("Правило (что запрещено / требуется)") },
        singleLine = false,
    )
    OutlinedTextField(
        value = rationale,
        onValueChange = { rationale = it },
        modifier = Modifier.fillMaxWidth(),
        label = { Text("Почему (необязательно)") },
        singleLine = false,
    )
    Button(
        onClick = {
            onIntent(ChatIntent.AddInvariant(category, rule, rationale))
            rule = ""
            rationale = ""
        },
        enabled = rule.isNotBlank(),
    ) { Text("Добавить") }
}
