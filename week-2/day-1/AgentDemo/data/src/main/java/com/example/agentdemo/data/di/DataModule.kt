package com.example.agentdemo.data.di

import com.example.agentdemo.data.BuildConfig
import com.example.agentdemo.data.agent.AgentConfig
import com.example.agentdemo.data.llm.ZaiLlmClient
import com.example.agentdemo.domain.agent.Agent
import com.example.agentdemo.domain.agent.ChatAgent
import com.example.agentdemo.domain.llm.LlmClient
import com.example.agentdemo.domain.usecase.AskAgentUseCase
import kotlinx.serialization.json.Json
import okhttp3.OkHttpClient
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

    factory { AskAgentUseCase(agent = get()) }
}
