package com.example.llmdemo.ui.main

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.example.llmdemo.data.ControlConfig
import com.example.llmdemo.data.ControlConfigs
import com.example.llmdemo.data.DataRepository
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

/** Ответ одного варианта контроля вместе с метаданными для сравнения. */
data class VariantResult(
    val config: ControlConfig,
    val content: String,
    val finishReason: String,
    val completionTokens: Int,
    val totalTokens: Int,
    val isError: Boolean = false,
)

data class CompareUiState(
    val prompt: String = ControlConfigs.DEFAULT_PROMPT,
    val isRunning: Boolean = false,
    val runningLabel: String? = null,
    val results: List<VariantResult> = emptyList(),
)

class MainScreenViewModel(private val repository: DataRepository) : ViewModel() {
    private val _uiState = MutableStateFlow(CompareUiState())
    val uiState: StateFlow<CompareUiState> = _uiState.asStateFlow()

    fun onPromptChanged(text: String) {
        _uiState.update { it.copy(prompt = text) }
    }

    /** Отправляет один и тот же запрос во всех конфигурациях контроля по очереди. */
    fun runComparison() {
        val prompt = _uiState.value.prompt.trim()
        if (prompt.isEmpty() || _uiState.value.isRunning) return

        _uiState.update { it.copy(isRunning = true, results = emptyList(), runningLabel = null) }

        viewModelScope.launch {
            for (config in ControlConfigs.all) {
                _uiState.update { it.copy(runningLabel = config.label) }
                val result = try {
                    val r = repository.ask(prompt, config)
                    VariantResult(
                        config = config,
                        content = r.content,
                        finishReason = r.finishReason,
                        completionTokens = r.completionTokens,
                        totalTokens = r.totalTokens,
                    )
                } catch (e: Exception) {
                    VariantResult(
                        config = config,
                        content = "Ошибка: ${e.message}",
                        finishReason = "error",
                        completionTokens = 0,
                        totalTokens = 0,
                        isError = true,
                    )
                }
                _uiState.update { it.copy(results = it.results + result) }
            }
            _uiState.update { it.copy(isRunning = false, runningLabel = null) }
        }
    }
}
