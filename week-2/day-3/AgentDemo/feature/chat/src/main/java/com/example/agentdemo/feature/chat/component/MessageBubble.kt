package com.example.agentdemo.feature.chat.component

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.example.agentdemo.domain.model.Role
import com.example.agentdemo.feature.chat.UiMessage

/** Пузырь одного сообщения: пользователь справа (акцент), агент слева (нейтральный). */
@Composable
fun MessageBubble(
    item: UiMessage,
    modifier: Modifier = Modifier,
) {
    val message = item.message
    val isUser = message.role == Role.USER
    val bubbleColor = if (isUser) {
        MaterialTheme.colorScheme.primary
    } else {
        MaterialTheme.colorScheme.surfaceVariant
    }
    val textColor = if (isUser) {
        MaterialTheme.colorScheme.onPrimary
    } else {
        MaterialTheme.colorScheme.onSurface
    }
    val shape = RoundedCornerShape(
        topStart = 20.dp,
        topEnd = 20.dp,
        bottomStart = if (isUser) 20.dp else 6.dp,
        bottomEnd = if (isUser) 6.dp else 20.dp,
    )

    Column(
        modifier = modifier.fillMaxWidth(),
        horizontalAlignment = if (isUser) Alignment.End else Alignment.Start,
    ) {
        Surface(
            color = bubbleColor,
            shape = shape,
            modifier = Modifier.widthIn(max = 320.dp),
        ) {
            Text(
                text = message.text,
                color = textColor,
                style = MaterialTheme.typography.bodyLarge,
                modifier = Modifier.padding(horizontal = 16.dp, vertical = 11.dp),
            )
        }
        // Подпись с токенами: «факт» для ответов агента (из usage), «оценка» для остального.
        Text(
            text = buildString {
                append(formatTokens(item.tokens))
                append(if (item.exact) " ток. · факт" else " ток. · оценка")
            },
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(top = 2.dp, start = 4.dp, end = 4.dp),
        )
    }
}
