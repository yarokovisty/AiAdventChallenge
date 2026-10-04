package com.example.agentdemo.data.token

import com.example.agentdemo.domain.llm.LlmMessage
import com.example.agentdemo.domain.token.TokenEstimator
import kotlin.math.roundToInt

/**
 * Эвристическая оценка числа токенов без настоящего токенизатора GLM.
 *
 * Точного BPE-токенизатора модели в Kotlin нет, поэтому считаем приблизительно,
 * по классам символов: латиница/цифры у BPE «длиннее» (несколько символов на
 * токен), кириллица дробится мельче, пунктуация и прочие символы обычно идут
 * почти по токену на символ. Делители подобраны так, чтобы оценка держалась
 * близко к серверному `usage` на смешанном русско-английском тексте.
 */
class HeuristicTokenEstimator : TokenEstimator {

    override fun estimate(text: String): Int {
        if (text.isBlank()) return 0

        var latin = 0   // латинские буквы и цифры: ~4 символа на токен
        var cyrillic = 0 // кириллица: дробится мельче, ~2.8 символа на токен
        var other = 0    // пунктуация/символы/прочее: ~почти токен на символ
        for (ch in text) {
            when {
                ch.isWhitespace() -> Unit // пробелы обычно приклеиваются к соседнему токену
                ch in 'a'..'z' || ch in 'A'..'Z' || ch.isDigit() -> latin++
                ch in 'а'..'я' || ch in 'А'..'Я' || ch == 'ё' || ch == 'Ё' -> cyrillic++
                else -> other++
            }
        }

        val tokens = latin / LATIN_PER_TOKEN +
            cyrillic / CYRILLIC_PER_TOKEN +
            other / OTHER_PER_TOKEN
        return tokens.roundToInt().coerceAtLeast(1)
    }

    override fun estimateRequest(messages: List<LlmMessage>): Int {
        // Каждое сообщение в chat-формате стоит немного сверх текста (разметка роли),
        // а ответ дополнительно «праймится» — те же накладные, что в правилах OpenAI.
        val content = messages.sumOf { estimate(it.content) + PER_MESSAGE_OVERHEAD }
        return content + REPLY_PRIMING
    }

    private companion object {
        const val LATIN_PER_TOKEN = 4.0
        // 2.8 символа кириллицы на токен — подобрано по реальному `usage` GLM-4.6
        // (русский токенизируется плотнее латиницы, но не настолько, как 2.0).
        const val CYRILLIC_PER_TOKEN = 2.8
        const val OTHER_PER_TOKEN = 1.0
        const val PER_MESSAGE_OVERHEAD = 4
        const val REPLY_PRIMING = 3
    }
}
