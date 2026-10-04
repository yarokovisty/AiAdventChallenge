package com.example.agentdemo.data.context

import com.example.agentdemo.data.conversation.StoredMessage
import com.example.agentdemo.data.conversation.toDomain
import com.example.agentdemo.data.conversation.toStored
import com.example.agentdemo.domain.context.Branch
import com.example.agentdemo.domain.context.BranchingState
import com.example.agentdemo.domain.context.ContextState
import com.example.agentdemo.domain.context.ContextStrategy
import com.example.agentdemo.domain.context.Fact
import com.example.agentdemo.domain.context.Facts
import kotlinx.serialization.Serializable

/**
 * Сериализуемое представление состояния управления контекстом для хранения на диске.
 *
 * Отдельные DTO нужны, чтобы доменные модели ([ContextState], [Facts], [Branch])
 * оставались чистыми и не тянули аннотации kotlinx.serialization: формат хранения —
 * забота слоя data (как и со [StoredMessage]).
 */
@Serializable
data class StoredContextState(
    val strategy: ContextStrategy = ContextStrategy.SLIDING_WINDOW,
    val facts: List<StoredFact> = emptyList(),
    val branching: StoredBranching = StoredBranching(),
)

@Serializable
data class StoredFact(
    val key: String,
    val value: String,
)

@Serializable
data class StoredBranch(
    val id: String,
    val name: String,
    val messages: List<StoredMessage> = emptyList(),
)

@Serializable
data class StoredBranching(
    val branches: List<StoredBranch> = emptyList(),
    val activeId: String? = null,
    val checkpointIndex: Int? = null,
)

/** Доменное состояние → хранимое. */
fun ContextState.toStored(): StoredContextState = StoredContextState(
    strategy = strategy,
    facts = facts.items.map { StoredFact(it.key, it.value) },
    branching = StoredBranching(
        branches = branching.branches.map { b ->
            StoredBranch(id = b.id, name = b.name, messages = b.messages.map { it.toStored() })
        },
        activeId = branching.activeId,
        checkpointIndex = branching.checkpointIndex,
    ),
)

/** Хранимое состояние → доменное. */
fun StoredContextState.toDomain(): ContextState = ContextState(
    strategy = strategy,
    facts = Facts(facts.map { Fact(it.key, it.value) }),
    branching = BranchingState(
        branches = branching.branches.map { b ->
            Branch(id = b.id, name = b.name, messages = b.messages.map { it.toDomain() })
        },
        activeId = branching.activeId,
        checkpointIndex = branching.checkpointIndex,
    ),
)
