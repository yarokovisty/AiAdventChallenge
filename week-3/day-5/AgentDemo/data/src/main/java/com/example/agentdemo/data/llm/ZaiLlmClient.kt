package com.example.agentdemo.data.llm

import com.example.agentdemo.data.remote.dto.ChatMessageDto
import com.example.agentdemo.data.remote.dto.ChatRequestDto
import com.example.agentdemo.data.remote.dto.ChatResponseDto
import com.example.agentdemo.data.remote.dto.ThinkingDto
import com.example.agentdemo.domain.llm.LlmClient
import com.example.agentdemo.domain.llm.LlmRequest
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody

/**
 * Реализация порта [LlmClient] поверх Z.ai (coding-эндпоинт, OpenAI-совместимый).
 *
 * Отвечает только за транспорт: сериализацию запроса, HTTP-вызов и разбор ответа.
 * Бизнес-логику агента не знает.
 */
class ZaiLlmClient(
    private val apiKey: String,
    private val json: Json,
    private val client: OkHttpClient,
) : LlmClient {

    override suspend fun complete(request: LlmRequest): String = withContext(Dispatchers.IO) {
        val dto = ChatRequestDto(
            model = request.model,
            messages = request.messages.map { ChatMessageDto(it.role, it.content) },
            temperature = request.temperature,
            thinking = ThinkingDto(type = "disabled"),
        )
        val body = json.encodeToString(ChatRequestDto.serializer(), dto)
            .toRequestBody(JSON_MEDIA_TYPE)

        val httpRequest = Request.Builder()
            .url(ENDPOINT)
            .addHeader("Authorization", "Bearer $apiKey")
            .post(body)
            .build()

        client.newCall(httpRequest).execute().use { response ->
            val raw = response.body?.string().orEmpty()
            if (raw.isBlank()) {
                error("Пустой ответ сервера (HTTP ${response.code})")
            }
            val parsed = json.decodeFromString(ChatResponseDto.serializer(), raw)
            parsed.error?.let { err ->
                error("Ошибка API [${err.code}]: ${err.message}")
            }
            parsed.choices.firstOrNull()?.message?.content
                ?: error("Ответ модели не содержит текста")
        }
    }

    private companion object {
        const val ENDPOINT = "https://api.z.ai/api/coding/paas/v4/chat/completions"
        val JSON_MEDIA_TYPE = "application/json".toMediaType()
    }
}
