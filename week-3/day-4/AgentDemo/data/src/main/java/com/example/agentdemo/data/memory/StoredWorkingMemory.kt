package com.example.agentdemo.data.memory

import com.example.agentdemo.domain.memory.WorkingMemory
import kotlinx.serialization.Serializable

/**
 * Сериализуемое представление рабочей памяти (одна активная задача + заметки).
 */
@Serializable
data class StoredWorkingMemory(
    val task: String,
    val notes: List<String> = emptyList(),
)

fun WorkingMemory.toStored(): StoredWorkingMemory =
    StoredWorkingMemory(task = task, notes = notes)

fun StoredWorkingMemory.toDomain(): WorkingMemory =
    WorkingMemory(task = task, notes = notes)
