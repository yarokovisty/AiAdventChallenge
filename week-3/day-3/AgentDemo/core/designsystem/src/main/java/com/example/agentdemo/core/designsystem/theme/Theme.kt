package com.example.agentdemo.core.designsystem.theme

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

private val LightColors = lightColorScheme(
    primary = Color(0xFF4F46E5),
    onPrimary = Color(0xFFFFFFFF),
    primaryContainer = Color(0xFFE0E7FF),
    onPrimaryContainer = Color(0xFF1E1B4B),
    secondaryContainer = Color(0xFFEEF2FF),
    onSecondaryContainer = Color(0xFF312E81),
    background = Color(0xFFFAFAFC),
    onBackground = Color(0xFF1A1A1E),
    surface = Color(0xFFFFFFFF),
    onSurface = Color(0xFF1A1A1E),
    surfaceVariant = Color(0xFFF1F2F6),
    onSurfaceVariant = Color(0xFF45464F),
    outlineVariant = Color(0xFFE3E4EA),
    error = Color(0xFFDC2626),
    onError = Color(0xFFFFFFFF),
)

private val DarkColors = darkColorScheme(
    primary = Color(0xFFA5B4FC),
    onPrimary = Color(0xFF1E1B4B),
    primaryContainer = Color(0xFF3730A3),
    onPrimaryContainer = Color(0xFFE0E7FF),
    secondaryContainer = Color(0xFF2A2D3A),
    onSecondaryContainer = Color(0xFFE0E7FF),
    background = Color(0xFF0F1014),
    onBackground = Color(0xFFE4E5EA),
    surface = Color(0xFF17181D),
    onSurface = Color(0xFFE4E5EA),
    surfaceVariant = Color(0xFF26272F),
    onSurfaceVariant = Color(0xFFC4C6D0),
    outlineVariant = Color(0xFF2E2F38),
    error = Color(0xFFF87171),
    onError = Color(0xFF1A1A1E),
)

/** Минималистичная тема приложения (индиго-акцент, нейтральные поверхности). */
@Composable
fun AgentDemoTheme(
    darkTheme: Boolean = isSystemInDarkTheme(),
    content: @Composable () -> Unit,
) {
    MaterialTheme(
        colorScheme = if (darkTheme) DarkColors else LightColors,
        typography = AgentTypography,
        content = content,
    )
}
