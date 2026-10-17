package com.example.agentdemo

import android.app.Application
import com.example.agentdemo.data.di.dataModule
import com.example.agentdemo.feature.chat.di.chatModule
import org.koin.android.ext.koin.androidContext
import org.koin.android.ext.koin.androidLogger
import org.koin.core.logger.Level
import org.koin.core.context.startKoin

/** Точка сборки DI-графа: объединяет модули всех слоёв. */
class AgentApp : Application() {
    override fun onCreate() {
        super.onCreate()
        startKoin {
            androidLogger(Level.ERROR)
            androidContext(this@AgentApp)
            modules(dataModule, chatModule)
        }
    }
}
