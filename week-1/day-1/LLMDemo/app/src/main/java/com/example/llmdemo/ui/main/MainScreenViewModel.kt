package com.example.llmdemo.ui.main

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.example.llmdemo.data.DataRepository
import com.example.llmdemo.data.Message
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

data class ChatUiState(
    val messages: List<Message> = emptyList(),
    val inputText: String = "",
    val isLoading: Boolean = false,
)

class MainScreenViewModel(private val repository: DataRepository) : ViewModel() {
    private val _uiState = MutableStateFlow(ChatUiState())
    val uiState: StateFlow<ChatUiState> = _uiState.asStateFlow()

    fun onInputChanged(text: String) {
        _uiState.update { it.copy(inputText = text) }
    }

    fun sendMessage() {
        val text = _uiState.value.inputText.trim()
        if (text.isEmpty() || _uiState.value.isLoading) return

        val updatedMessages = _uiState.value.messages + Message(text, isFromUser = true)
        _uiState.update { it.copy(messages = updatedMessages, inputText = "", isLoading = true) }

        viewModelScope.launch {
            val response = try {
                repository.ask(updatedMessages)
            } catch (e: Exception) {
                "Ошибка: ${e.message}"
            }
            _uiState.update { state ->
                state.copy(
                    messages = state.messages + Message(response, isFromUser = false),
                    isLoading = false,
                )
            }
        }
    }
}
