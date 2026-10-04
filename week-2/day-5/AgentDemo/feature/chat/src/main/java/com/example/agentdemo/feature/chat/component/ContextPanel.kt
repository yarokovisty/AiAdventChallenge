package com.example.agentdemo.feature.chat.component

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.KeyboardArrowDown
import androidx.compose.material.icons.filled.KeyboardArrowUp
import androidx.compose.material3.AssistChip
import androidx.compose.material3.Button
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.SegmentedButton
import androidx.compose.material3.SegmentedButtonDefaults
import androidx.compose.material3.SingleChoiceSegmentedButtonRow
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.example.agentdemo.domain.context.ContextStrategy
import com.example.agentdemo.feature.chat.ChatIntent
import com.example.agentdemo.feature.chat.ChatState

/**
 * Панель управления контекстом над лентой: выбор одной из трёх стратегий,
 * строка-метрика последней отправки и элементы, специфичные для стратегии
 * (факты — для Sticky Facts, чекпоинт/ветки — для Branching).
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ContextPanel(
    state: ChatState,
    onIntent: (ChatIntent) -> Unit,
    modifier: Modifier = Modifier,
) {
    Surface(
        modifier = modifier.fillMaxWidth(),
        color = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.4f),
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 16.dp, vertical = 8.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            StrategySelector(state.strategy, onIntent)
            StatusLine(state)

            when (state.strategy) {
                ContextStrategy.STICKY_FACTS -> FactsSection(state, onIntent)
                ContextStrategy.BRANCHING -> BranchingSection(state, onIntent)
                ContextStrategy.SLIDING_WINDOW -> Unit
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun StrategySelector(
    selected: ContextStrategy,
    onIntent: (ChatIntent) -> Unit,
) {
    val options = listOf(
        ContextStrategy.SLIDING_WINDOW to "Окно",
        ContextStrategy.STICKY_FACTS to "Факты",
        ContextStrategy.BRANCHING to "Ветки",
    )
    SingleChoiceSegmentedButtonRow(modifier = Modifier.fillMaxWidth()) {
        options.forEachIndexed { index, (strategy, label) ->
            SegmentedButton(
                selected = selected == strategy,
                onClick = { onIntent(ChatIntent.SelectStrategy(strategy)) },
                shape = SegmentedButtonDefaults.itemShape(index = index, count = options.size),
            ) {
                Text(label)
            }
        }
    }
}

@Composable
private fun StatusLine(state: ChatState) {
    val stats = state.lastStats
    val description = when (state.strategy) {
        ContextStrategy.SLIDING_WINDOW ->
            "Окно: последние ${state.windowSize} реплик, старое отбрасывается"

        ContextStrategy.STICKY_FACTS ->
            "Факты (${state.facts.items.size}) + окно ${state.windowSize} реплик"

        ContextStrategy.BRANCHING -> {
            val active = state.branching.branches.firstOrNull { it.id == state.branching.activeId }
            if (active != null) {
                "Ветка «${active.name}»: вся история (${state.messages.size} реплик)"
            } else {
                "Ветвление: поставьте чекпоинт и создайте 2 ветки"
            }
        }
    }
    val tokens = stats?.let { " · последняя отправка ≈${it.estimatedTokens} ток. (${it.sentMessages} реплик)" }.orEmpty()

    Text(
        text = description + tokens,
        style = MaterialTheme.typography.labelMedium,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )
}

@Composable
private fun FactsSection(
    state: ChatState,
    onIntent: (ChatIntent) -> Unit,
) {
    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = androidx.compose.ui.Alignment.CenterVertically,
        ) {
            Text(
                text = "Липкие факты (${state.facts.items.size})",
                style = MaterialTheme.typography.titleSmall,
            )
            OutlinedButton(onClick = { onIntent(ChatIntent.ToggleFacts) }) {
                Text(if (state.showFacts) "Скрыть" else "Показать")
                Icon(
                    imageVector = if (state.showFacts) Icons.Filled.KeyboardArrowUp else Icons.Filled.KeyboardArrowDown,
                    contentDescription = null,
                )
            }
        }
        AnimatedVisibility(visible = state.showFacts) {
            Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
                if (state.facts.isEmpty) {
                    Text(
                        text = "Факты появятся после ваших сообщений — агент сам выделит важное.",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                } else {
                    state.facts.items.forEach { fact ->
                        Text(
                            text = "• ${fact.key}: ${fact.value}",
                            style = MaterialTheme.typography.bodySmall,
                        )
                    }
                }
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun BranchingSection(
    state: ChatState,
    onIntent: (ChatIntent) -> Unit,
) {
    val branching = state.branching
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedButton(
                onClick = { onIntent(ChatIntent.Checkpoint) },
                enabled = !branching.isForked,
            ) {
                Text("Чекпоинт")
            }
            Button(
                onClick = { onIntent(ChatIntent.Fork) },
                enabled = !branching.isForked,
            ) {
                Text("Создать 2 ветки")
            }
        }

        branching.checkpointIndex?.let { index ->
            AssistChip(
                onClick = {},
                label = { Text("Чекпоинт: сообщение №$index") },
            )
        }

        if (branching.isForked) {
            LazyRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                items(branching.branches) { branch ->
                    val isActive = branch.id == branching.activeId
                    // У активной ветки сообщения живут в state.messages (снапшот ветки
                    // синхронизируется лишь при сохранении), поэтому показываем живой счётчик.
                    val count = if (isActive) state.messages.size else branch.messages.size
                    FilterChip(
                        selected = isActive,
                        onClick = { onIntent(ChatIntent.SwitchBranch(branch.id)) },
                        label = {
                            Text(
                                text = "${branch.name} ($count)",
                                fontWeight = if (isActive) FontWeight.Bold else FontWeight.Normal,
                            )
                        },
                    )
                }
            }
        }
    }
}
