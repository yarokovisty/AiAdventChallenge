package com.example.agentdemo.feature.chat

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.Chat
import androidx.compose.material.icons.outlined.DeleteOutline
import androidx.compose.material3.CenterAlignedTopAppBar
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.example.agentdemo.domain.model.Message
import com.example.agentdemo.feature.chat.component.ChatInputBar
import com.example.agentdemo.feature.chat.component.MessageBubble
import com.example.agentdemo.feature.chat.component.TypingIndicator
import kotlinx.coroutines.flow.Flow
import org.koin.androidx.compose.koinViewModel

/** Точка входа фичи: подключает ViewModel (Koin) и связывает его с UI. */
@Composable
fun ChatRoute(
    modifier: Modifier = Modifier,
    viewModel: ChatViewModel = koinViewModel(),
) {
    val state by viewModel.state.collectAsStateWithLifecycle()
    ChatScreen(
        state = state,
        effects = viewModel.effects,
        onIntent = viewModel::onIntent,
        modifier = modifier,
    )
}

/** Stateless-экран чата: рисует состояние и шлёт намерения наверх. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ChatScreen(
    state: ChatState,
    effects: Flow<ChatEffect>,
    onIntent: (ChatIntent) -> Unit,
    modifier: Modifier = Modifier,
) {
    val listState = rememberLazyListState()
    val snackbarHostState = remember { SnackbarHostState() }

    // Одноразовые эффекты (показ ошибки).
    LaunchedEffect(Unit) {
        effects.collect { effect ->
            when (effect) {
                is ChatEffect.ShowError -> snackbarHostState.showSnackbar(effect.message)
            }
        }
    }

    // Автоскролл к последнему элементу при каждом новом сообщении и при появлении
    // индикатора «печатает». Ключи — пара (размер списка, isSending): так скролл
    // срабатывает и когда ответ приходит на смену индикатору (число элементов тогда
    // не меняется, но isSending переключается с true на false).
    LaunchedEffect(state.messages.size, state.isSending) {
        val itemCount = state.messages.size + if (state.isSending) 1 else 0
        if (itemCount > 0) listState.animateScrollToItem(itemCount - 1)
    }

    Scaffold(
        modifier = modifier.fillMaxSize(),
        topBar = {
            CenterAlignedTopAppBar(
                title = {
                    Column(horizontalAlignment = Alignment.CenterHorizontally) {
                        Text(
                            text = state.agentName,
                            style = MaterialTheme.typography.titleMedium,
                        )
                        Text(
                            text = state.agentRole,
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                },
                actions = {
                    if (state.messages.isNotEmpty()) {
                        IconButton(onClick = { onIntent(ChatIntent.Clear) }) {
                            Icon(
                                imageVector = Icons.Outlined.DeleteOutline,
                                contentDescription = "Очистить диалог",
                            )
                        }
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = MaterialTheme.colorScheme.surface,
                ),
            )
        },
        bottomBar = {
            ChatInputBar(
                value = state.input,
                enabled = !state.isSending,
                canSend = state.canSend,
                onValueChange = { onIntent(ChatIntent.InputChanged(it)) },
                onSend = { onIntent(ChatIntent.Send) },
            )
        },
        snackbarHost = { SnackbarHost(snackbarHostState) },
        containerColor = MaterialTheme.colorScheme.background,
    ) { innerPadding ->
        if (state.messages.isEmpty() && !state.isSending) {
            EmptyState(modifier = Modifier.padding(innerPadding))
        } else {
            MessageList(
                messages = state.messages,
                isSending = state.isSending,
                listState = listState,
                contentPadding = innerPadding,
            )
        }
    }
}

@Composable
private fun MessageList(
    messages: List<Message>,
    isSending: Boolean,
    listState: androidx.compose.foundation.lazy.LazyListState,
    contentPadding: PaddingValues,
) {
    LazyColumn(
        modifier = Modifier.fillMaxSize(),
        state = listState,
        contentPadding = PaddingValues(
            top = contentPadding.calculateTopPadding() + 12.dp,
            bottom = contentPadding.calculateBottomPadding() + 12.dp,
            start = 16.dp,
            end = 16.dp,
        ),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        items(messages) { message ->
            MessageBubble(message = message)
        }
        if (isSending) {
            item { TypingIndicator() }
        }
    }
}

@Composable
private fun EmptyState(modifier: Modifier = Modifier) {
    Box(
        modifier = modifier.fillMaxSize(),
        contentAlignment = Alignment.Center,
    ) {
        Column(
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(12.dp),
            modifier = Modifier.padding(32.dp),
        ) {
            Icon(
                imageVector = Icons.AutoMirrored.Outlined.Chat,
                contentDescription = null,
                tint = MaterialTheme.colorScheme.primary,
                modifier = Modifier.size(48.dp),
            )
            Text(
                text = "Задайте агенту любой вопрос",
                style = MaterialTheme.typography.titleMedium,
                textAlign = TextAlign.Center,
            )
            Text(
                text = "Он отправит запрос в LLM и вернёт ответ.",
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                textAlign = TextAlign.Center,
            )
        }
    }
}
