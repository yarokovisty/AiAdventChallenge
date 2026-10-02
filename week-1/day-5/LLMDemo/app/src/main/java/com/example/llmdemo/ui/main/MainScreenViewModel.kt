package com.example.llmdemo.ui.main

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.example.llmdemo.data.DataRepository
import com.example.llmdemo.data.ModelConfig
import com.example.llmdemo.data.ModelsCatalog
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

/** Результат прогона одной модели: ответ + метрики (время, токены, стоимость, правильность). */
data class ModelResult(
    val config: ModelConfig,
    val content: String,
    val promptTokens: Int,
    val completionTokens: Int,
    val totalTokens: Int,
    val latencyMs: Long,
    val costUsd: Double,
    val correctness: Boolean?,
    val isError: Boolean = false,
) {
    /** Стоимость 1000 таких же запросов — чтобы крошечные числа стали наглядными. */
    val costPer1000Usd: Double get() = costUsd * 1000
}

data class CompareUiState(
    val prompt: String = ModelsCatalog.DEFAULT_PROMPT,
    val isRunning: Boolean = false,
    val runningLabel: String? = null,
    val results: List<ModelResult> = emptyList(),
)

class MainScreenViewModel(private val repository: DataRepository) : ViewModel() {
    private val _uiState = MutableStateFlow(CompareUiState())
    val uiState: StateFlow<CompareUiState> = _uiState.asStateFlow()

    fun onPromptChanged(text: String) {
        _uiState.update { it.copy(prompt = text) }
    }

    /** Прогоняет один и тот же запрос последовательно по всем моделям каталога. */
    fun runComparison() {
        val prompt = _uiState.value.prompt.trim()
        if (prompt.isEmpty() || _uiState.value.isRunning) return

        _uiState.update { it.copy(isRunning = true, results = emptyList(), runningLabel = null) }

        viewModelScope.launch {
            for (model in ModelsCatalog.all) {
                _uiState.update { it.copy(runningLabel = "Запрос к ${model.label}…") }
                val result = try {
                    val r = repository.ask(prompt, model)
                    val cost = r.promptTokens / 1_000_000.0 * model.priceInputPerM +
                        r.completionTokens / 1_000_000.0 * model.priceOutputPerM
                    ModelResult(
                        config = model,
                        content = r.content,
                        promptTokens = r.promptTokens,
                        completionTokens = r.completionTokens,
                        totalTokens = r.totalTokens,
                        latencyMs = r.latencyMs,
                        costUsd = cost,
                        correctness = ModelsCatalog.isCorrect(r.content),
                    )
                } catch (e: Exception) {
                    ModelResult(
                        config = model,
                        content = "Ошибка: ${e.message}",
                        promptTokens = 0,
                        completionTokens = 0,
                        totalTokens = 0,
                        latencyMs = 0,
                        costUsd = 0.0,
                        correctness = null,
                        isError = true,
                    )
                }
                _uiState.update { it.copy(results = it.results + result) }
            }
            _uiState.update { it.copy(isRunning = false, runningLabel = null) }
        }
    }
}
