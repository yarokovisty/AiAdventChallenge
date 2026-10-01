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
    /** Решает одну и ту же [problem] выбранным [strategy] способом рассуждения. */
    suspend fun solve(problem: String, strategy: ReasoningStrategy): StrategyResult
}

/** Внутреннее сообщение диалога для сборки тела запроса. */
private data class Msg(val role: String, val content: String)

/** Ответ одного обращения к модели: текст + потраченные токены. */
private data class ChatResponse(val content: String, val completionTokens: Int, val totalTokens: Int)

class DefaultDataRepository : DataRepository {
    private val client = OkHttpClient.Builder()
        .connectTimeout(30, TimeUnit.SECONDS)
        .readTimeout(120, TimeUnit.SECONDS)
        .writeTimeout(30, TimeUnit.SECONDS)
        .build()

    override suspend fun solve(problem: String, strategy: ReasoningStrategy): StrategyResult =
        withContext(Dispatchers.IO) {
            when (strategy) {
                ReasoningStrategy.DIRECT -> solveDirect(problem)
                ReasoningStrategy.STEP_BY_STEP -> solveStepByStep(problem)
                ReasoningStrategy.SELF_PROMPT -> solveSelfPrompt(problem)
                ReasoningStrategy.EXPERTS -> solveExperts(problem)
            }
        }

    // 1. Прямой ответ — один вызов, никаких инструкций по рассуждению.
    private fun solveDirect(problem: String): StrategyResult {
        val r = chat(listOf(Msg("user", problem)))
        return StrategyResult(
            strategy = ReasoningStrategy.DIRECT,
            finalAnswer = r.content,
            completionTokens = r.completionTokens,
            totalTokens = r.totalTokens,
        )
    }

    // 2. Пошагово — один вызов + инструкция рассуждать по шагам.
    private fun solveStepByStep(problem: String): StrategyResult {
        val r = chat(
            listOf(
                Msg(
                    "system",
                    "Решай задачу пошагово. Разбей решение на явные шаги и рассуждай по ним. " +
                        "В самом конце выведи отдельной строкой: «Ответ: <итог>».",
                ),
                Msg("user", problem),
            ),
        )
        return StrategyResult(
            strategy = ReasoningStrategy.STEP_BY_STEP,
            finalAnswer = r.content,
            completionTokens = r.completionTokens,
            totalTokens = r.totalTokens,
        )
    }

    // 3. Самопромпт — вызов A: модель составляет промпт; вызов B: решает этим промптом.
    private fun solveSelfPrompt(problem: String): StrategyResult {
        val crafted = chat(
            listOf(
                Msg(
                    "system",
                    "Ты — эксперт по промпт-инжинирингу. Тебе дадут задачу. " +
                        "НЕ решай её. Вместо этого составь максимально эффективный промпт, " +
                        "который поможет языковой модели решить такую задачу точно и без ошибок. " +
                        "Выведи только текст промпта, без пояснений.",
                ),
                Msg("user", "Задача: $problem"),
            ),
        )
        val generatedPrompt = crafted.content

        val solved = chat(
            listOf(
                Msg("system", generatedPrompt),
                Msg("user", problem),
            ),
        )

        return StrategyResult(
            strategy = ReasoningStrategy.SELF_PROMPT,
            finalAnswer = solved.content,
            steps = listOf(
                ReasoningStep("Сгенерированный моделью промпт", generatedPrompt),
            ),
            completionTokens = crafted.completionTokens + solved.completionTokens,
            totalTokens = crafted.totalTokens + solved.totalTokens,
        )
    }

    // 4. Группа экспертов — последовательная цепочка: аналитик → инженер → критик (финал).
    private fun solveExperts(problem: String): StrategyResult {
        val analysis = chat(
            listOf(
                Msg(
                    "system",
                    "Ты — Аналитик. Разбери задачу: что дано, что требуется найти, " +
                        "в чём подвох и на что обязательно обратить внимание при решении. " +
                        "НЕ давай окончательный ответ — только разбор.",
                ),
                Msg("user", "Задача: $problem"),
            ),
        )

        val solution = chat(
            listOf(
                Msg(
                    "system",
                    "Ты — Инженер. Опираясь на разбор аналитика, реши задачу пошагово и дай ответ.",
                ),
                Msg(
                    "user",
                    "Задача: $problem\n\nРазбор аналитика:\n${analysis.content}",
                ),
            ),
        )

        val critique = chat(
            listOf(
                Msg(
                    "system",
                    "Ты — Критик. Проверь решение инженера на ошибки. " +
                        "Если нашёл ошибку — исправь и поясни. " +
                        "В конце выведи отдельной строкой проверенный итог: «Ответ: <итог>».",
                ),
                Msg(
                    "user",
                    "Задача: $problem\n\nРазбор аналитика:\n${analysis.content}\n\n" +
                        "Решение инженера:\n${solution.content}",
                ),
            ),
        )

        return StrategyResult(
            strategy = ReasoningStrategy.EXPERTS,
            finalAnswer = critique.content,
            steps = listOf(
                ReasoningStep("Аналитик — разбор задачи", analysis.content),
                ReasoningStep("Инженер — решение", solution.content),
            ),
            completionTokens = analysis.completionTokens + solution.completionTokens + critique.completionTokens,
            totalTokens = analysis.totalTokens + solution.totalTokens + critique.totalTokens,
        )
    }

    /** Один обмен с моделью. Reasoning отключён — иначе «прямой ответ» тайно рассуждал бы. */
    private fun chat(messages: List<Msg>): ChatResponse {
        val messagesArray = JSONArray().apply {
            messages.forEach { m ->
                put(JSONObject().apply {
                    put("role", m.role)
                    put("content", m.content)
                })
            }
        }

        val body = JSONObject().apply {
            put("model", "glm-5.3")
            put("messages", messagesArray)
            // GLM-5.3 — reasoning-модель. Для честного сравнения способов рассуждения
            // встроенное «размышление» отключаем: иначе даже «прямой ответ» получал бы
            // скрытую цепочку рассуждений, и разница между способами исчезла бы.
            put("thinking", JSONObject().put("type", "disabled"))
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

        return ChatResponse(
            content = content,
            completionTokens = usage?.optInt("completion_tokens", 0) ?: 0,
            totalTokens = usage?.optInt("total_tokens", 0) ?: 0,
        )
    }
}
