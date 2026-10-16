package com.example.agentdemo.feature.chat.component

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Delete
import androidx.compose.material.icons.outlined.Edit
import androidx.compose.material3.AssistChip
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
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
import com.example.agentdemo.domain.profile.UserProfile
import com.example.agentdemo.feature.chat.ChatIntent
import com.example.agentdemo.feature.chat.ChatState

/**
 * Панель профилей пользователя — слой персонализации поверх памяти.
 *
 * Здесь пользователь переключает активный профиль (именно он автоматически
 * уходит в каждый запрос) и редактирует предпочтения: кто он, стиль, формат,
 * ограничения. Переключение профиля меняет стиль ответов, не трогая память —
 * так наглядно видно «ответы для разных профилей».
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ProfileSheet(
    state: ChatState,
    onIntent: (ChatIntent) -> Unit,
) {
    val sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    // Профиль, открытый в редакторе (null — редактор свёрнут).
    var editing by remember { mutableStateOf<UserProfile?>(null) }

    ModalBottomSheet(
        onDismissRequest = { onIntent(ChatIntent.CloseProfiles) },
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
                text = "Профиль пользователя",
                style = MaterialTheme.typography.titleLarge,
            )
            Text(
                text = "Активный профиль подключается к каждому запросу автоматически — " +
                    "агент подстраивает стиль, формат и ограничения под вас, " +
                    "даже если вы не повторяете их в сообщении.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )

            state.profiles.forEach { profile ->
                ProfileCard(
                    profile = profile,
                    isActive = profile.id == state.activeProfileId,
                    onSelect = { onIntent(ChatIntent.SelectProfile(profile.id)) },
                    onEdit = { editing = profile },
                    onDelete = { onIntent(ChatIntent.DeleteProfile(profile.id)) },
                )
            }

            HorizontalDivider(modifier = Modifier.padding(vertical = 8.dp))

            ProfileEditor(
                editing = editing,
                onSave = { onIntent(ChatIntent.SaveProfile(it)); editing = null },
                onCancel = { editing = null },
                onNew = { editing = EMPTY_PROFILE },
            )
        }
    }
}

private val EMPTY_PROFILE = UserProfile(id = "", name = "")

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun ProfileCard(
    profile: UserProfile,
    isActive: Boolean,
    onSelect: () -> Unit,
    onEdit: () -> Unit,
    onDelete: () -> Unit,
) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(
            containerColor = if (isActive) MaterialTheme.colorScheme.primaryContainer
            else MaterialTheme.colorScheme.surfaceVariant,
        ),
    ) {
        Column(modifier = Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    text = profile.name,
                    style = MaterialTheme.typography.titleMedium,
                    fontWeight = FontWeight.SemiBold,
                    modifier = Modifier.weight(1f),
                )
                IconButton(onClick = onEdit) {
                    Icon(Icons.Outlined.Edit, contentDescription = "Редактировать профиль")
                }
                IconButton(onClick = onDelete) {
                    Icon(Icons.Outlined.Delete, contentDescription = "Удалить профиль")
                }
            }
            if (profile.style.isNotBlank()) {
                Text("Стиль: ${profile.style}", style = MaterialTheme.typography.bodySmall)
            }
            if (profile.format.isNotBlank()) {
                Text("Формат: ${profile.format}", style = MaterialTheme.typography.bodySmall)
            }
            if (profile.constraints.isNotBlank()) {
                Text("Ограничения: ${profile.constraints}", style = MaterialTheme.typography.bodySmall)
            }
            FilterChip(
                selected = isActive,
                onClick = onSelect,
                label = { Text(if (isActive) "Активен" else "Сделать активным") },
            )
        }
    }
}

@Composable
private fun ProfileEditor(
    editing: UserProfile?,
    onSave: (UserProfile) -> Unit,
    onCancel: () -> Unit,
    onNew: () -> Unit,
) {
    if (editing == null) {
        OutlinedButton(onClick = onNew) { Text("+ Новый профиль") }
        return
    }

    val isNew = editing.id.isBlank()
    var name by remember(editing) { mutableStateOf(editing.name) }
    var persona by remember(editing) { mutableStateOf(editing.persona) }
    var style by remember(editing) { mutableStateOf(editing.style) }
    var format by remember(editing) { mutableStateOf(editing.format) }
    var constraints by remember(editing) { mutableStateOf(editing.constraints) }

    Text(
        text = if (isNew) "Новый профиль" else "Редактирование профиля",
        style = MaterialTheme.typography.titleMedium,
    )

    Field("Название профиля", name, singleLine = true) { name = it }
    Field("Кто пользователь", persona) { persona = it }
    Field("Стиль ответа", style) { style = it }
    Field("Формат ответа", format) { format = it }
    Field("Ограничения", constraints) { constraints = it }

    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        Button(
            onClick = {
                onSave(
                    editing.copy(
                        name = name.trim(),
                        persona = persona.trim(),
                        style = style.trim(),
                        format = format.trim(),
                        constraints = constraints.trim(),
                    ),
                )
            },
            enabled = name.isNotBlank(),
        ) { Text("Сохранить") }
        AssistChip(onClick = onCancel, label = { Text("Отмена") })
    }
}

@Composable
private fun Field(
    label: String,
    value: String,
    singleLine: Boolean = false,
    onValueChange: (String) -> Unit,
) {
    OutlinedTextField(
        value = value,
        onValueChange = onValueChange,
        modifier = Modifier.fillMaxWidth(),
        label = { Text(label) },
        singleLine = singleLine,
    )
}
