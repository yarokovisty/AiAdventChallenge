package com.example.agentdemo.data.di

import com.example.agentdemo.data.BuildConfig
import com.example.agentdemo.data.agent.AgentConfig
import com.example.agentdemo.data.conversation.JsonConversationRepository
import com.example.agentdemo.data.llm.ZaiLlmClient
import com.example.agentdemo.data.memory.JsonLongTermMemoryRepository
import com.example.agentdemo.data.memory.JsonWorkingMemoryRepository
import com.example.agentdemo.data.profile.JsonUserProfileRepository
import com.example.agentdemo.data.task.JsonTaskStateRepository
import com.example.agentdemo.domain.agent.Agent
import com.example.agentdemo.domain.agent.ChatAgent
import com.example.agentdemo.domain.conversation.ConversationRepository
import com.example.agentdemo.domain.llm.LlmClient
import com.example.agentdemo.domain.memory.LongTermMemoryRepository
import com.example.agentdemo.domain.memory.WorkingMemoryRepository
import com.example.agentdemo.domain.profile.UserProfileRepository
import com.example.agentdemo.domain.task.TaskStateRepository
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

    factory { AskAgentUseCase(agent = get()) }

    // Три слоя памяти — каждый в своём JSON-файле во внутренней памяти.
    // Краткосрочная: история диалога (conversation.json).
    single<ConversationRepository> {
        JsonConversationRepository(context = androidContext(), json = get())
    }

    // Долговременная: профиль, решения, знания (long_term_memory.json).
    single<LongTermMemoryRepository> {
        JsonLongTermMemoryRepository(context = androidContext(), json = get())
    }

    // Рабочая: текущая задача и заметки по ней (working_memory.json).
    single<WorkingMemoryRepository> {
        JsonWorkingMemoryRepository(context = androidContext(), json = get())
    }

    // Персонализация поверх памяти: профили пользователя (user_profiles.json).
    single<UserProfileRepository> {
        JsonUserProfileRepository(context = androidContext(), json = get())
    }

    // Состояние задачи как конечный автомат: этап/шаг/действие (task_state.json).
    single<TaskStateRepository> {
        JsonTaskStateRepository(context = androidContext(), json = get())
    }
}
