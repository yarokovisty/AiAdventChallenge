package com.example.llmdemo.ui.main

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.example.llmdemo.data.DataRepository
import com.example.llmdemo.data.TemperatureConfig
import com.example.llmdemo.data.TemperatureConfigs
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

/** Один прогон одной температуры. */
data class TempRun(
    val index: Int,
    val content: String,
    val completionTokens: Int,
    val isError: Boolean = false,
)

/** Все прогоны одной температуры вместе с метрикой разнообразия. */
data class TempVariantResult(
    val config: TemperatureConfig,
    val runs: List<TempRun>,
) {
    /** Сколько уникальных ответов среди прогонов — главный показатель «разнообразия». */
    val distinctCount: Int
        get() = runs.filterNot { it.isError }
            .map { it.content.trim() }
            .distinct()
            .size

    val diversityLabel: String
        get() = when {
            runs.any { it.isError } -> "есть ошибки"
            distinctCount <= 1 -> "все $totalRuns ответа идентичны"
            distinctCount == totalRuns -> "все $totalRuns ответа разные"
            else -> "$distinctCount уник. из $totalRuns"
        }

    private val totalRuns: Int get() = runs.size
}

data class CompareUiState(
    val prompt: String = TemperatureConfigs.DEFAULT_PROMPT,
    val isRunning: Boolean = false,
    val runningLabel: String? = null,
    val results: List<TempVariantResult> = emptyList(),
)

class MainScreenViewModel(private val repository: DataRepository) : ViewModel() {
    private val _uiState = MutableStateFlow(CompareUiState())
    val uiState: StateFlow<CompareUiState> = _uiState.asStateFlow()

    fun onPromptChanged(text: String) {
        _uiState.update { it.copy(prompt = text) }
    }

    /**
     * Отправляет один и тот же запрос при каждой температуре по [TemperatureConfigs.RUNS_PER_TEMPERATURE]
     * раз — чтобы разброс (разнообразие) ответов был виден напрямую.
     */
    fun runComparison() {
        val prompt = _uiState.value.prompt.trim()
        if (prompt.isEmpty() || _uiState.value.isRunning) return

        _uiState.update { it.copy(isRunning = true, results = emptyList(), runningLabel = null) }

        viewModelScope.launch {
            val runs = TemperatureConfigs.RUNS_PER_TEMPERATURE
            for (config in TemperatureConfigs.all) {
                val collected = mutableListOf<TempRun>()
                for (i in 1..runs) {
                    _uiState.update { it.copy(runningLabel = "${config.label} — прогон $i/$runs") }
                    val run = try {
                        val r = repository.ask(prompt, config)
                        TempRun(index = i, content = r.content, completionTokens = r.completionTokens)
                    } catch (e: Exception) {
                        TempRun(index = i, content = "Ошибка: ${e.message}", completionTokens = 0, isError = true)
                    }
                    collected += run
                    val snapshot = collected.toList()
                    _uiState.update { state ->
                        val others = state.results.filterNot { it.config == config }
                        state.copy(results = others + TempVariantResult(config, snapshot))
                    }
                }
            }
            _uiState.update { it.copy(isRunning = false, runningLabel = null) }
        }
    }
}
