package com.example.agentdemo.feature.chat.di

import com.example.agentdemo.feature.chat.ChatViewModel
import org.koin.core.module.dsl.viewModel
import org.koin.dsl.module

/** Koin-граф фичи чата. */
val chatModule = module {
    viewModel {
        ChatViewModel(
            askAgent = get(),
            conversation = get(),
            agentMemory = get(),
            agent = get(),
        )
    }
}
