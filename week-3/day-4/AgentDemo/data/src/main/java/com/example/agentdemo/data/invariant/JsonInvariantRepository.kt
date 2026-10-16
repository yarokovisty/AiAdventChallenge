package com.example.agentdemo.data.invariant

import android.content.Context
import com.example.agentdemo.domain.invariant.Invariant
import com.example.agentdemo.domain.invariant.InvariantCategory
import com.example.agentdemo.domain.invariant.InvariantRepository
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import java.io.File

/**
 * Инварианты в отдельном JSON-файле (`filesDir/invariants.json`).
 *
 * Реализация порта [InvariantRepository]. Слой инвариантов физически отделён от
 * диалога и остальной памяти — требование задания: правила живут независимо и
 * переживают очистку разговора. При первом запуске засевает набор инвариантов
 * проекта (по одному на каждую категорию из условия), чтобы сразу было чем
 * проверить отказ при конфликте. Ввод-вывод — на [Dispatchers.IO].
 */
class JsonInvariantRepository(
    context: Context,
    private val json: Json,
) : InvariantRepository {

    private val file: File = File(context.filesDir, FILE_NAME)
    private val serializer = StoredInvariants.serializer()

    override suspend fun loadAll(): List<Invariant> = withContext(Dispatchers.IO) {
        read().invariants.map { it.toDomain() }
    }

    override suspend fun save(invariant: Invariant) = withContext(Dispatchers.IO) {
        val current = read()
        val updated = current.invariants.filterNot { it.id == invariant.id } + invariant.toStored()
        write(current.copy(invariants = updated))
    }

    override suspend fun delete(id: String) = withContext(Dispatchers.IO) {
        val current = read()
        write(current.copy(invariants = current.invariants.filterNot { it.id == id }))
    }

    override suspend fun setActive(id: String, isActive: Boolean) = withContext(Dispatchers.IO) {
        val current = read()
        val updated = current.invariants.map {
            if (it.id == id) it.copy(isActive = isActive) else it
        }
        write(current.copy(invariants = updated))
    }

    override suspend fun clear() = withContext(Dispatchers.IO) {
        file.delete()
        Unit
    }

    /** Читает файл; при первом запуске создаёт и сохраняет инварианты по умолчанию. */
    private fun read(): StoredInvariants {
        if (!file.exists()) {
            val seeded = seedDefaults()
            write(seeded)
            return seeded
        }
        return runCatching { json.decodeFromString(serializer, file.readText()) }
            .getOrDefault(StoredInvariants()) // битый файл не должен ронять старт
    }

    private fun write(data: StoredInvariants) {
        file.writeText(json.encodeToString(serializer, data))
    }

    /**
     * Инварианты проекта по умолчанию — по одному на каждую категорию из задания.
     * Это реальные правила AgentDemo, поэтому отказы агента на их нарушение
     * выглядят осмысленно (напр. «перепиши DI на Hilt» конфликтует с техрешением).
     */
    private fun seedDefaults(): StoredInvariants {
        val defaults = listOf(
            StoredInvariant(
                id = "seed-architecture",
                category = InvariantCategory.ARCHITECTURE.slug,
                rule = "Приложение строится по Clean Architecture с разделением на модули " +
                    "domain / data / feature и паттерном MVI в presentation-слое. " +
                    "Доменный слой не зависит от Android и не содержит аннотаций сериализации.",
                rationale = "Единая архитектура всего проекта; её нарушение ломает границы модулей.",
            ),
            StoredInvariant(
                id = "seed-tech-decision",
                category = InvariantCategory.TECH_DECISION.slug,
                rule = "Dependency Injection выполняется только через Koin. " +
                    "Hilt/Dagger и ручная сборка графа зависимостей запрещены.",
                rationale = "Принятое техническое решение проекта; смешивать DI-фреймворки нельзя.",
            ),
            StoredInvariant(
                id = "seed-stack",
                category = InvariantCategory.STACK.slug,
                rule = "Исходный код пишется только на Kotlin. Добавлять файлы на Java " +
                    "в исходники запрещено. UI — только Jetpack Compose, без View/XML-вёрстки.",
                rationale = "Ограничение по стеку: единый язык и единый UI-фреймворк.",
            ),
            StoredInvariant(
                id = "seed-business-rule",
                category = InvariantCategory.BUSINESS_RULE.slug,
                rule = "API-ключ Z.ai не должен попадать в исходный код или в репозиторий. " +
                    "Ключ берётся только из .env/BuildConfig и никогда не логируется.",
                rationale = "Требование безопасности: утечка ключа = компрометация доступа к API.",
            ),
        )
        return StoredInvariants(invariants = defaults)
    }

    private companion object {
        const val FILE_NAME = "invariants.json"
    }
}
