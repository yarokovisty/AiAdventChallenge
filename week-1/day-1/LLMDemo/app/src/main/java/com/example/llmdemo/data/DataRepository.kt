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
    suspend fun ask(messages: List<Message>): String
}

class DefaultDataRepository : DataRepository {
    private val client = OkHttpClient.Builder()
        .connectTimeout(30, TimeUnit.SECONDS)
        .readTimeout(120, TimeUnit.SECONDS)
        .writeTimeout(30, TimeUnit.SECONDS)
        .build()

    override suspend fun ask(messages: List<Message>): String = withContext(Dispatchers.IO) {
        val messagesArray = JSONArray()
        messages.forEach { msg ->
            messagesArray.put(JSONObject().apply {
                put("role", if (msg.isFromUser) "user" else "assistant")
                put("content", msg.content)
            })
        }

        val body = JSONObject().apply {
            put("model", "glm-5.3")
            put("messages", messagesArray)
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
        json.getJSONArray("choices")
            .getJSONObject(0)
            .getJSONObject("message")
            .getString("content")
    }
}
