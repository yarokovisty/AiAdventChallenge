package com.example.agentdemo.data.di

import com.example.agentdemo.data.BuildConfig
import com.example.agentdemo.data.agent.AgentConfig
import com.example.agentdemo.data.context.JsonContextStateRepository
import com.example.agentdemo.data.context.LlmFactsExtractor
import com.example.agentdemo.data.conversation.JsonConversationRepository
import com.example.agentdemo.data.llm.ZaiLlmClient
import com.example.agentdemo.data.token.HeuristicTokenEstimator
import com.example.agentdemo.domain.agent.Agent
import com.example.agentdemo.domain.agent.ChatAgent
import com.example.agentdemo.domain.context.ContextManager
import com.example.agentdemo.domain.context.ContextStateRepository
import com.example.agentdemo.domain.context.FactsExtractor
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

    single<Agent> {
        ChatAgent(
            llmClient = get(),
            name = AgentConfig.NAME,
            role = AgentConfig.ROLE,
            systemPrompt = AgentConfig.SYSTEM_PROMPT,
            model = AgentConfig.MODEL,
        )
    }

    // Оценка токенов — метрика для сравнения стратегий по их расходу.
    single<TokenEstimator> { HeuristicTokenEstimator() }

    // Извлечение «липких фактов» — отдельный дешёвый запрос к той же модели.
    single<FactsExtractor> { LlmFactsExtractor(llmClient = get(), json = get(), model = AgentConfig.MODEL) }

    // Доменный сервис стратегий управления контекстом (окно / факты / ветки).
    single { ContextManager(windowSize = ContextManager.DEFAULT_WINDOW) }

    factory {
        AskAgentUseCase(
            agent = get(),
            contextManager = get(),
            factsExtractor = get(),
            tokenEstimator = get(),
        )
    }

    // Хранилище истории диалога (JSON-файл во внутренней памяти) —
    // контекст агента переживает перезапуск приложения.
    single<ConversationRepository> {
        JsonConversationRepository(context = androidContext(), json = get())
    }

    // Хранилище состояния управления контекстом (стратегия + факты + ветки).
    single<ContextStateRepository> {
        JsonContextStateRepository(context = androidContext(), json = get())
    }
}
