package com.example.agentdemo.data.invariant

import com.example.agentdemo.domain.invariant.Invariant
import com.example.agentdemo.domain.invariant.InvariantCategory
import kotlinx.serialization.Serializable

/**
 * Сериализуемое представление инварианта для хранения на диске.
 *
 * Отдельный DTO, чтобы доменная модель [Invariant] оставалась чистым Kotlin без
 * аннотаций kotlinx.serialization. Категория хранится латинским ключом
 * ([InvariantCategory.slug]) — файл остаётся читаемым и устойчивым к
 * переименованию констант enum; неизвестный ключ трактуется как бизнес-правило.
 */
@Serializable
data class StoredInvariant(
    val id: String,
    val category: String,
    val rule: String,
    val rationale: String = "",
    val isActive: Boolean = true,
)

/** Весь слой инвариантов в одном файле. */
@Serializable
data class StoredInvariants(
    val invariants: List<StoredInvariant> = emptyList(),
)

fun Invariant.toStored(): StoredInvariant = StoredInvariant(
    id = id,
    category = category.slug,
    rule = rule,
    rationale = rationale,
    isActive = isActive,
)

fun StoredInvariant.toDomain(): Invariant = Invariant(
    id = id,
    category = InvariantCategory.entries.firstOrNull { it.slug == category }
        ?: InvariantCategory.BUSINESS_RULE,
    rule = rule,
    rationale = rationale,
    isActive = isActive,
)
