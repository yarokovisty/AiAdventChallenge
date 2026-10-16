package com.example.agentdemo

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import com.example.agentdemo.core.designsystem.theme.AgentDemoTheme
import com.example.agentdemo.feature.chat.ChatRoute

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            AgentDemoTheme {
                ChatRoute()
            }
        }
    }
}
