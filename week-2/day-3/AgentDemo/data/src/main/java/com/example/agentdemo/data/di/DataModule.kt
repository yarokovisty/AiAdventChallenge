package com.example.agentdemo.data.di

import com.example.agentdemo.data.BuildConfig
import com.example.agentdemo.data.agent.AgentConfig
import com.example.agentdemo.data.conversation.JsonConversationRepository
import com.example.agentdemo.data.llm.ZaiLlmClient
import com.example.agentdemo.data.token.HeuristicTokenEstimator
import com.example.agentdemo.domain.agent.Agent
import com.example.agentdemo.domain.agent.ChatAgent
import com.example.agentdemo.domain.conversation.ConversationRepository
import com.example.agentdemo.domain.llm.LlmClient
import com.example.agentdemo.domain.token.TokenEstimator
import com.example.agentdemo.domain.usecase.AskAgentUseCase
import kotlinx.serialization.json.Json
import okhttp3.OkHttpClient
import org.koin.android.ext.koin.androidContext
import org.koin.dsl.module
import java.util.concurrent.TimeUnit

/** Koin-граф слоя data: транспорт, порт LLM, сам агент и use case. */
val dataModule = module {

    single {
        Json {
            ignoreUnknownKeys = true
            encodeDefaults = true
            explicitNulls = false
        }
    }

    single {
        OkHttpClient.Builder()
            .connectTimeout(30, TimeUnit.SECONDS)
            .readTimeout(120, TimeUnit.SECONDS) // GLM может отвечать долго на длинных запросах
            .writeTimeout(30, TimeUnit.SECONDS)
            .build()
    }

    single<LlmClient> {
        ZaiLlmClient(
            apiKey = BuildConfig.ZAI_API_KEY,
            json = get(),
            client = get(),
        )
    }

    // Локальная оценка токенов: нужна агенту (прогноз запроса) и ViewModel (UI).
    single<TokenEstimator> { HeuristicTokenEstimator() }

    single<Agent> {
        ChatAgent(
            llmClient = get(),
            tokenEstimator = get(),
            name = AgentConfig.NAME,
            role = AgentConfig.ROLE,
            systemPrompt = AgentConfig.SYSTEM_PROMPT,
            model = AgentConfig.MODEL,
        )
    }

    factory { AskAgentUseCase(agent = get()) }

    // Хранилище истории диалога (JSON-файл во внутренней памяти) —
    // контекст агента переживает перезапуск приложения.
    single<ConversationRepository> {
        JsonConversationRepository(context = androidContext(), json = get())
    }
}
