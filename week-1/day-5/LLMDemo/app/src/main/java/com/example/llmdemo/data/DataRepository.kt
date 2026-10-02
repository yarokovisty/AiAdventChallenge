package com.example.llmdemo.data

import com.example.llmdemo.BuildConfig
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.TimeUnit

interface DataRepository {
    /** Отправляет один и тот же [prompt] на конкретную [model]; замеряет время и токены. */
    suspend fun ask(prompt: String, model: ModelConfig): AskResult
}

class DefaultDataRepository : DataRepository {
    private val client = OkHttpClient.Builder()
        .connectTimeout(30, TimeUnit.SECONDS)
        .readTimeout(120, TimeUnit.SECONDS)
        .writeTimeout(30, TimeUnit.SECONDS)
        .build()

    override suspend fun ask(prompt: String, model: ModelConfig): AskResult =
        withContext(Dispatchers.IO) {
            val messagesArray = JSONArray().apply {
                put(JSONObject().apply {
                    put("role", "user")
                    put("content", prompt)
                })
            }

            val body = JSONObject().apply {
                put("model", model.id)
                put("messages", messagesArray)
                // Отключаем «размышление»: сравниваем прямой ответ моделей по скорости,
                // токенам и стоимости. Reasoning раздул бы токены/время неравномерно.
                put("thinking", JSONObject().put("type", "disabled"))
            }.toString().toRequestBody("application/json".toMediaType())

            val request = Request.Builder()
                .url("https://api.z.ai/api/coding/paas/v4/chat/completions")
                .addHeader("Authorization", "Bearer ${BuildConfig.API_KEY}")
                .post(body)
                .build()

            val startNs = System.nanoTime()
            val response = client.newCall(request).execute()
            val responseBody = response.body?.string() ?: ""
            val latencyMs = (System.nanoTime() - startNs) / 1_000_000

            val json = JSONObject(responseBody)
            if (json.has("error")) {
                val err = json.getJSONObject("error")
                throw Exception("API error [${err.optString("code")}]: ${err.optString("message")}")
            }

            val choice = json.getJSONArray("choices").getJSONObject(0)
            val content = choice.getJSONObject("message").getString("content")
            val usage = json.optJSONObject("usage")

            AskResult(
                content = content,
                promptTokens = usage?.optInt("prompt_tokens", 0) ?: 0,
                completionTokens = usage?.optInt("completion_tokens", 0) ?: 0,
                totalTokens = usage?.optInt("total_tokens", 0) ?: 0,
                latencyMs = latencyMs,
            )
        }
}
