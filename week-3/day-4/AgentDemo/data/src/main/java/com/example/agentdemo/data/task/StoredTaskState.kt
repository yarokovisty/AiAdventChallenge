package com.example.agentdemo.data.task

import com.example.agentdemo.domain.task.TaskStage
import com.example.agentdemo.domain.task.TaskState
import kotlinx.serialization.Serializable

/**
 * Сериализуемое представление состояния задачи (конечного автомата).
 *
 * Этап хранится латинским ключом ([TaskStage.slug]) — так файл остаётся читаемым
 * и устойчивым к переименованию констант enum. Неизвестный ключ при чтении
 * трактуется как «планирование» (безопасный старт автомата).
 */
@Serializable
data class StoredTaskState(
    val goal: String,
    val stage: String,
    val currentStep: String,
    val expectedAction: String,
    val isPaused: Boolean = false,
    val log: List<String> = emptyList(),
)

fun TaskState.toStored(): StoredTaskState = StoredTaskState(
    goal = goal,
    stage = stage.slug,
    currentStep = currentStep,
    expectedAction = expectedAction,
    isPaused = isPaused,
    log = log,
)

fun StoredTaskState.toDomain(): TaskState = TaskState(
    goal = goal,
    stage = TaskStage.entries.firstOrNull { it.slug == stage } ?: TaskStage.PLANNING,
    currentStep = currentStep,
    expectedAction = expectedAction,
    isPaused = isPaused,
    log = log,
)
