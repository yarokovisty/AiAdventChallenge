package com.example.agentdemo.data.di

import com.example.agentdemo.data.BuildConfig
import com.example.agentdemo.data.agent.AgentConfig
import com.example.agentdemo.data.context.JsonAgentMemoryRepository
import com.example.agentdemo.data.context.LlmSummarizer
import com.example.agentdemo.data.conversation.JsonConversationRepository
import com.example.agentdemo.data.llm.ZaiLlmClient
import com.example.agentdemo.data.token.HeuristicTokenEstimator
import com.example.agentdemo.domain.agent.Agent
import com.example.agentdemo.domain.agent.ChatAgent
import com.example.agentdemo.domain.context.AgentMemoryRepository
import com.example.agentdemo.domain.context.ContextCompressor
import com.example.agentdemo.domain.context.ContextPolicy
import com.example.agentdemo.domain.context.Summarizer
import com.example.agentdemo.domain.conversation.ConversationRepository
import com.example.agentdemo.domain.llm.LlmClient
import com.example.agentdemo.domain.token.TokenEstimator
import com.example.agentdemo.domain.usecase.AskAgentUseCase
import kotlinx.serialization.json.Json
import okhttp3.OkHttpClient
import org.koin.android.ext.koin.androidContext
import org.koin.dsl.module
import java.util.concurrent.TimeUnit

/** Koin-граф слоя data: транспорт, порт LLM, агент, управление контекстом и use case. */
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

    single<Agent> {
        ChatAgent(
            llmClient = get(),
            name = AgentConfig.NAME,
            role = AgentConfig.ROLE,
            systemPrompt = AgentConfig.SYSTEM_PROMPT,
            model = AgentConfig.MODEL,
        )
    }

    // --- Управление контекстом (Week 2 · Day 4) ---

    // Политика сжатия: сколько свежих реплик держим «как есть» и как часто
    // сворачиваем старьё в summary.
    single { ContextPolicy() }

    // Локальная оценка токенов — для количественного сравнения режимов.
    single<TokenEstimator> { HeuristicTokenEstimator() }

    // Сжатие части диалога в summary — отдельный вызов LLM на той же модели.
    single<Summarizer> { LlmSummarizer(llmClient = get(), model = AgentConfig.MODEL) }

    single { ContextCompressor(summarizer = get(), policy = get()) }

    // Summary хранится ОТДЕЛЬНО от ленты — в своём JSON-файле.
    single<AgentMemoryRepository> {
        JsonAgentMemoryRepository(context = androidContext(), json = get())
    }

    factory {
        AskAgentUseCase(
            agent = get(),
            compressor = get(),
            tokenEstimator = get(),
        )
    }

    // Хранилище истории диалога (JSON-файл во внутренней памяти) —
    // контекст агента переживает перезапуск приложения.
    single<ConversationRepository> {
        JsonConversationRepository(context = androidContext(), json = get())
    }
}
