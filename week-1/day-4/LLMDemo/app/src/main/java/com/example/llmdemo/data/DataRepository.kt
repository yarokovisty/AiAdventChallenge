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
    /** Отправляет один и тот же [prompt] с sampling-параметрами из [config]. */
    suspend fun ask(prompt: String, config: TemperatureConfig): AskResult
}

class DefaultDataRepository : DataRepository {
    private val client = OkHttpClient.Builder()
        .connectTimeout(30, TimeUnit.SECONDS)
        .readTimeout(120, TimeUnit.SECONDS)
        .writeTimeout(30, TimeUnit.SECONDS)
        .build()

    override suspend fun ask(prompt: String, config: TemperatureConfig): AskResult =
        withContext(Dispatchers.IO) {
            val messagesArray = JSONArray().apply {
                put(JSONObject().apply {
                    put("role", "user")
                    put("content", prompt)
                })
            }

            val body = JSONObject().apply {
                put("model", "glm-5.3")
                put("messages", messagesArray)
                // GLM-5.3 — reasoning-модель: по умолчанию «размышление» включено. Тогда
                // temperature влияет на скрытый reasoning, а видимый ответ остаётся стабильным,
                // и сравнение теряет смысл. Отключаем — температура действует на сам ответ.
                put("thinking", JSONObject().put("type", "disabled"))
                put("temperature", config.temperature)
                config.topP?.let { put("top_p", it) }
            }.toString().toRequestBody("application/json".toMediaType())

            val request = Request.Builder()
                .url("https://api.z.ai/api/coding/paas/v4/chat/completions")
                .addHeader("Authorization", "Bearer ${BuildConfig.API_KEY}")
                .post(body)
                .build()

            val response = client.newCall(request).execute()
            val responseBody = response.body?.string() ?: ""
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
                completionTokens = usage?.optInt("completion_tokens", 0) ?: 0,
            )
        }
}
