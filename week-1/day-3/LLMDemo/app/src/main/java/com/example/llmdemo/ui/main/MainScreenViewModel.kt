package com.example.llmdemo.ui.main

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.example.llmdemo.data.AnswerCheck
import com.example.llmdemo.data.DataRepository
import com.example.llmdemo.data.ReasoningStrategy
import com.example.llmdemo.data.ReasoningTask
import com.example.llmdemo.data.StrategyResult
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

/** Итог прогона одного способа несколько раз: извлечённые ответы + сколько совпало с эталоном. */
data class StabilityResult(
    val strategy: ReasoningStrategy,
    val answers: List<String>,
    val correctCount: Int,
    val total: Int,
)

data class CompareUiState(
    val problem: String = ReasoningTask.DEFAULT_PROBLEM,
    val referenceAnswer: String = ReasoningTask.REFERENCE_ANSWER,
    val isRunning: Boolean = false,
    val runningLabel: String? = null,
    val results: List<StrategyResult> = emptyList(),
    val stability: List<StabilityResult> = emptyList(),
)

class MainScreenViewModel(private val repository: DataRepository) : ViewModel() {
    private val _uiState = MutableStateFlow(CompareUiState())
    val uiState: StateFlow<CompareUiState> = _uiState.asStateFlow()

    fun onProblemChanged(text: String) {
        _uiState.update { it.copy(problem = text) }
    }

    /** Один прогон: решает задачу всеми четырьмя способами по очереди (с полными карточками). */
    fun runComparison() {
        val problem = _uiState.value.problem.trim()
        if (problem.isEmpty() || _uiState.value.isRunning) return

        _uiState.update {
            it.copy(isRunning = true, results = emptyList(), stability = emptyList(), runningLabel = null)
        }

        viewModelScope.launch {
            for (strategy in ReasoningStrategy.entries) {
                _uiState.update { it.copy(runningLabel = strategy.label) }
                val result = try {
                    repository.solve(problem, strategy)
                } catch (e: Exception) {
                    StrategyResult(
                        strategy = strategy,
                        finalAnswer = "Ошибка: ${e.message}",
                        isError = true,
                    )
                }
                _uiState.update { it.copy(results = it.results + result) }
            }
            _uiState.update { it.copy(isRunning = false, runningLabel = null) }
        }
    }

    /** Прогоняет каждый способ [times] раз и собирает статистику стабильности ответов. */
    fun runStability(times: Int) {
        val problem = _uiState.value.problem.trim()
        if (problem.isEmpty() || _uiState.value.isRunning) return

        _uiState.update {
            it.copy(isRunning = true, results = emptyList(), stability = emptyList(), runningLabel = null)
        }

        viewModelScope.launch {
            val reference = _uiState.value.referenceAnswer
            for (strategy in ReasoningStrategy.entries) {
                val answers = mutableListOf<String>()
                for (i in 1..times) {
                    _uiState.update { it.copy(runningLabel = "${strategy.label} — прогон $i/$times") }
                    val answer = try {
                        AnswerCheck.extractAnswer(repository.solve(problem, strategy).finalAnswer) ?: "?"
                    } catch (e: Exception) {
                        "ошибка"
                    }
                    answers.add(answer)
                }
                val correct = answers.count { it == reference }
                _uiState.update {
                    it.copy(
                        stability = it.stability + StabilityResult(strategy, answers, correct, times),
                    )
                }
            }
            _uiState.update { it.copy(isRunning = false, runningLabel = null) }
        }
    }
}
