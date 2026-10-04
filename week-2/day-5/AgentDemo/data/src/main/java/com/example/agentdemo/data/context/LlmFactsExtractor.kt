package com.example.agentdemo.data.context

import com.example.agentdemo.domain.context.Fact
import com.example.agentdemo.domain.context.Facts
import com.example.agentdemo.domain.context.FactsExtractor
import com.example.agentdemo.domain.llm.LlmClient
import com.example.agentdemo.domain.llm.LlmMessage
import com.example.agentdemo.domain.llm.LlmRequest
import com.example.agentdemo.domain.model.Message
import com.example.agentdemo.domain.model.Role
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive

/**
 * Реализация порта [FactsExtractor] поверх [LlmClient].
 *
 * Отдельным дешёвым запросом к модели (низкая температура) поддерживает актуальный
 * блок «липких фактов»: отдаёт текущие факты и новое сообщение пользователя и
 * просит вернуть обновлённый набор ключ→значение в виде JSON-объекта. Это
 * честный +1 запрос на каждое сообщение пользователя — он виден в расходе токенов,
 * что как раз и нужно для сравнения стратегий.
 *
 * Разбор устойчив к «болтовне» модели вокруг JSON: вырезаем подстроку от первой
 * `{` до последней `}`. При любой ошибке возвращаем прежние факты — обновление
 * фактов не должно ронять основной ответ агента.
 */
class LlmFactsExtractor(
    private val llmClient: LlmClient,
    private val json: Json,
    private val model: String,
) : FactsExtractor {

    override suspend fun update(
        previous: Facts,
        userMessage: String,
        recentContext: List<Message>,
    ): Facts {
        val contextBlock = recentContext.joinToString("\n") { msg ->
            val who = when (msg.role) {
                Role.USER -> "Пользователь"
                Role.AGENT -> "Ассистент"
            }
            "$who: ${msg.text}"
        }

        val userContent = buildString {
            if (!previous.isEmpty) {
                appendLine("Текущие факты (JSON):")
                appendLine(previous.toJsonString())
                appendLine()
            }
            if (contextBlock.isNotBlank()) {
                appendLine("Последние реплики диалога (для контекста):")
                appendLine(contextBlock)
                appendLine()
            }
            appendLine("Новое сообщение пользователя:")
            append(userMessage)
        }

        val messages = listOf(
            LlmMessage(role = "system", content = EXTRACT_PROMPT),
            LlmMessage(role = "user", content = userContent),
        )

        val raw = runCatching {
            llmClient.complete(LlmRequest(model = model, messages = messages, temperature = 0.1))
        }.getOrElse { return previous }

        return parseFacts(raw) ?: previous
    }

    private fun parseFacts(raw: String): Facts? {
        val start = raw.indexOf('{')
        val end = raw.lastIndexOf('}')
        if (start < 0 || end <= start) return null
        return runCatching {
            val obj = json.parseToJsonElement(raw.substring(start, end + 1)).jsonObject
            val items = obj.entries.mapNotNull { (key, value) ->
                val text = value.jsonPrimitive.content.trim()
                if (key.isBlank() || text.isBlank()) null else Fact(key.trim(), text)
            }
            Facts(items)
        }.getOrNull()
    }

    private fun Facts.toJsonString(): String =
        items.joinToString(prefix = "{", postfix = "}") { "\"${it.key}\": \"${it.value}\"" }

    private companion object {
        val EXTRACT_PROMPT = """
            Ты ведёшь компактную память диалога в виде фактов ключ→значение.
            Тебе дают текущие факты и новое сообщение пользователя. Верни ОБНОВЛЁННЫЙ
            полный набор фактов как JSON-объект {"ключ":"значение"} и НИЧЕГО больше.

            Правила:
            - храни только важные, устойчивые данные: цель, требования ТЗ, ограничения,
              предпочтения, принятые решения, договорённости, ключевые числа/имена/сроки;
            - обновляй значения, если пользователь их изменил; не теряй старые факты,
              если они всё ещё в силе;
            - ключи — короткие и по-русски (например: "цель", "срок", "бюджет",
              "платформа", "стиль", "решение"); значения — краткие;
            - не добавляй болтовню, пояснения, markdown или ```-ограждения;
            - если важных фактов пока нет, верни пустой объект {}.
        """.trimIndent()
    }
}
