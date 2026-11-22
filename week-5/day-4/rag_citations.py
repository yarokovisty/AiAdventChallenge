"""
Week 5 · Day 4 — Цитаты, источники и анти-галлюцинации.

Задание: доработать RAG так, чтобы модель возвращала (1) ответ, (2) список
источников (source + section/chunk_id) и (3) дословные цитаты из найденных
чанков; на 10 контрольных вопросах проверить наличие источников и цитат, а
также соответствие смысла ответа цитатам; при релевантности ниже порога
ассистент говорит «не знаю» и просит уточнение.

Пайплайн (база — full-режим day-3: rewrite → поиск → LLM-реранкер с порогом):
  вопрос ─► [1] query rewrite (glm-4.6; rewrite используется ТОЛЬКО для поиска)
         ─► [2] векторный поиск (e5) → топ-K1 = 8 кандидатов
         ─► [3] LLM-реранкер 0–10 + ПОРОГ (2, плато 2–4 из sweep day-3) →
                топ-K2 = 3 чанка в контекст
                └─ ничего не прошло порог → «не знаю» + просьба уточнить
                   (детерминированно, генерация не вызывается вообще)
         ─► [4] генерация со СТРОГИМ JSON-выходом: answer + sources
                [{chunk_id, doc, section}] + quotes [{chunk_id, text}] —
                промпт требует дословные цитаты и настоящие chunk_id
         ─► [5] ПОСТ-ВАЛИДАЦИЯ (анти-галлюцинации, детерминированно, без LLM):
                • источники: chunk_id существует в контексте, doc/section
                  совпадают с метаданными чанка — сослаться на несуществующее
                  не выйдет;
                • цитаты: текст реально содержится в указанном чанке
                  (нормализация пробелов/регистра/кавычек + нечёткое совпадение
                  на мелкие LLM-искажения, difflib);
                битые ссылки и цитаты выкидываются и логируются как пойманные
                галлюцинации
         ─► [6] faithfulness-судья (один LLM-вызов): подтверждается ли смысл
                ответа валидными цитатами → supported / partial / unsupported;
                unsupported → ответ отвергается: «не знаю» + просьба уточнить.

Контроль: 10 корпусных вопросов day-2 (наличие/валидность источников и цитат,
вердикты судьи, факты) + 3 вне-корпусных day-3 (пустой контекст, «не знаю»
и просьба уточнить).

Запуск:
    python rag_citations.py                    # полный прогон 10+3 вопросов
    python rag_citations.py --question "..."   # демо пайплайна на одном вопросе
    python rag_citations.py --k1 10 --k2 4     # другие top-K до/после отбора
    python rag_citations.py --threshold 4      # другой порог релевантности

Индекс — из day-1 (structured, 42 чанка, intfloat/multilingual-e5-small),
вопросы — из eval_questions.json. Ключ ZAI_API_KEY хранится в .env
(gitignored), в git не попадает.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import requests
from dotenv import load_dotenv

# Тихий режим кэша HuggingFace на Windows (как в day-1/day-2/day-3).
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_INDEX = BASE_DIR.parent / "day-1" / "index" / "structured.json"
QUESTIONS_FILE = BASE_DIR / "eval_questions.json"
RESULTS_DIR = BASE_DIR / "results"

# OpenAI-совместимый coding-эндпоинт Z.ai (тот же, что в day-2/day-3).
ZAI_CHAT_URL_DEFAULT = "https://api.z.ai/api/coding/paas/v4/chat/completions"
ZAI_MODEL_DEFAULT = "glm-4.6"
TEMPERATURE = 0.0
REQUEST_TIMEOUT = 120

K1_DEFAULT = 8            # top-K ДО отбора: сколько кандидатов достаёт поиск
K2_DEFAULT = 3            # top-K ПОСЛЕ отбора: сколько чанков попадает в контекст
LLM_THRESHOLD_DEFAULT = 2     # порог LLM-скора релевантности (0–10) — плато 2–4 из sweep day-3
QUOTE_MATCH_THRESHOLD = 0.90  # порог нечёткого совпадения цитаты с текстом чанка

VERDICTS = ("supported", "partial", "unsupported")
CLARIFY_MARKERS = ("уточни", "переформулируй", "конкретизируй", "уточнение")

REFUSAL_EMPTY_CTX_WITH_HINTS = (
    "Не знаю: в базе знаний курса нет релевантной информации по этому вопросу "
    "(ни один найденный фрагмент не прошёл порог релевантности). Пожалуйста, "
    "уточните или переформулируйте вопрос — возможно, вы имели в виду одну из "
    "тем базы: {hints}."
)
REFUSAL_EMPTY_CTX_PLAIN = (
    "Не знаю: в базе знаний курса нет релевантной информации по этому вопросу "
    "(ни один найденный фрагмент не прошёл порог релевантности). Пожалуйста, "
    "уточните или переформулируйте вопрос — например, назовите конкретный "
    "термин, инструмент или значение из тем курса (промптинг, агенты, "
    "управление контекстом, MCP, RAG), который вас интересует."
)
REFUSAL_UNFAITHFUL = (
    "Не знаю: не удалось подтвердить ответ дословными цитатами из базы знаний "
    "(смысл ответа не выводится из найденных фрагментов). Пожалуйста, "
    "уточните или переформулируйте вопрос."
)

# System-промпты. Rewrite и реранкер — те же, что в day-3 (честная преемственность).
SYSTEM_REWRITE = (
    "Ты — подготовитель поисковых запросов для базы знаний курса по AI "
    "(промптинг, агенты, управление контекстом, MCP, RAG). Перепиши вопрос "
    "пользователя в компактный поисковый запрос к этой базе: убери разговорные "
    "обороты и обращения, разверни расплывчатые формулировки и местоимения в "
    "конкретные термины темы, сохрани информационную потребность целиком. "
    "Ответь только самим поисковым запросом, одной строкой, без пояснений "
    "и кавычек."
)
SYSTEM_RERANK = (
    "Ты — строгий оценщик релевантности отрывков базы знаний. Тебе дан вопрос и "
    "пронумерованные отрывки. Оцени по шкале 0–10, насколько отрывок содержит "
    "информацию, нужную для ответа на вопрос: 0–2 — нерелевантен; 3–5 — по "
    "теме, но ответа не содержит; 6–7 — частично полезен; 8–10 — содержит "
    "существенную часть ответа. Оценивай строго: отрывок про другую тему — это "
    "0–2, каким бы гладким он ни был. Ответь строго JSON без пояснений: "
    '{"scores": [{"id": 1, "score": 7}, ...]} — по одному объекту на каждый '
    "отрывок, id — номер отрывка."
)
SYSTEM_ANSWER = (
    "Ты — ассистент с доступом к базе знаний курса. Тебе даны фрагменты базы "
    "(первая строка каждого фрагмента — [chunk_id | документ | раздел]) и "
    "вопрос. Ответь на вопрос ТОЛЬКО на основе этих фрагментов, по-русски, "
    "кратко (2–6 предложений). Верни строго JSON без markdown-обёрток и "
    "пояснений, точно такой структуры: "
    '{"answer": "текст ответа", "sources": [{"chunk_id": "…", "doc": "…", '
    '"section": "…"}], "quotes": [{"chunk_id": "…", "text": "…"}]}. '
    "Правила: sources — все фрагменты, из которых взята информация для ответа "
    "(1–3), поля chunk_id/doc/section копируй из первой строки фрагмента "
    "дословно; quotes — 1–3 ДОСЛОВНЫЕ цитаты из этих фрагментов, подтверждающие "
    "ответ (точная копия фрагмента текста, а не пересказ), каждая со своим "
    "chunk_id. Если ответа во фрагментах нет — верни "
    '{"answer": "", "sources": [], "quotes": []}.'
)
SYSTEM_FAITH = (
    "Ты — строгий оценщик обоснованности ответов. Даны вопрос, ответ "
    "ассистента и дословные цитаты из фрагментов базы знаний. Реши, "
    "подтверждается ли смысл ответа этими цитатами: supported — утверждения "
    "ответа выводятся из цитат; partial — часть утверждений подтверждается, "
    "часть нет; unsupported — ответ по сути не выводится из цитат "
    "(галлюцинация или общие знания). Стилистические расхождения и перестановка "
    "слов не считаются расхождением смысла. Ответь строго JSON без пояснений: "
    '{"verdict": "supported|partial|unsupported", "reason": "кратко, по-русски"}.'
)


# --------------------------------------------------------------------------- #
# Ошибки LLM-вызова (как в day-2/day-3: retry только на сетевых/серверных сбоях)
# --------------------------------------------------------------------------- #


class LlmRetryableError(RuntimeError):
    """Сетевой сбой или 429/5xx — имеет смысл повторить."""


class LlmFatalError(RuntimeError):
    """Ошибка без смысла повторять (нет ключа, 401, кривой запрос)."""


def llm_call(messages: list[dict], temperature: float = TEMPERATURE) -> str:
    api_key = os.getenv("ZAI_API_KEY")
    if not api_key or api_key == "your_api_key_here":
        raise LlmFatalError(
            "ZAI_API_KEY не задан: скопируйте .env.example в .env и впишите ключ."
        )
    url = os.getenv("ZAI_CHAT_URL", ZAI_CHAT_URL_DEFAULT)
    model = os.getenv("ZAI_MODEL", ZAI_MODEL_DEFAULT)
    body = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "thinking": {"type": "disabled"},  # короткие детерминированные ответы
    }
    for attempt in (1, 2):
        try:
            resp = requests.post(
                url,
                headers={"Authorization": f"Bearer {api_key}"},
                json=body,
                timeout=REQUEST_TIMEOUT,
            )
            if resp.status_code == 200:
                data = resp.json()
                choices = data.get("choices") or []
                content = ((choices[0].get("message") or {}).get("content") or "").strip()
                if not content:
                    raise LlmRetryableError(f"пустой content: {json.dumps(data)[:160]}")
                return content
            if resp.status_code == 429 or resp.status_code >= 500:
                raise LlmRetryableError(f"HTTP {resp.status_code}: {resp.text[:160]}")
            raise LlmFatalError(f"HTTP {resp.status_code}: {resp.text[:160]}")
        except LlmFatalError:
            raise
        except (requests.RequestException, LlmRetryableError) as exc:
            if attempt == 2:
                raise LlmFatalError(f"LLM недоступна после повтора: {exc}") from exc
            print(f"    [сбой запроса, повтор через 2 с: {exc}]")
            time.sleep(2)
    raise LlmFatalError("unreachable")


# --------------------------------------------------------------------------- #
# Индекс и поиск (как в day-2/day-3)
# --------------------------------------------------------------------------- #


def load_index(path: Path) -> dict:
    if not path.exists():
        sys.exit(
            f"[ОШИБКА] Индекс не найден: {path}\n"
            f"Сначала соберите его: cd {path.parent.parent / 'day-1'} && "
            f"python build_index.py"
        )
    return json.loads(path.read_text(encoding="utf-8"))


class QueryEmbedder:
    """Эмбеддер запросов — та же модель, что индексировала чанки (имя — в
    метаданных индекса). E5 требует префикс «query: » для поисковых запросов."""

    def __init__(self, model_name: str):
        from sentence_transformers import SentenceTransformer

        print(f"    Загрузка модели запросов {model_name} ...")
        self.model = SentenceTransformer(model_name)

    def embed_query(self, text: str) -> list[float]:
        vec = self.model.encode(
            [f"query: {text}"], normalize_embeddings=True, show_progress_bar=False
        )
        return vec[0].astype(float).tolist()


def vector_search(chunks: list[dict], query_vec: list[float], k: int) -> list[tuple[dict, float]]:
    """Топ-K чанков по косинусной близости (векторы нормированы)."""
    matrix = np.array([c["vector"] for c in chunks])
    sims = matrix @ np.array(query_vec)
    order = np.argsort(-sims)[:k]
    return [(chunks[i], float(sims[i])) for i in order]


def heading_of(chunk: dict) -> str:
    path = chunk.get("heading_path") or []
    return " › ".join(path[1:]) if len(path) > 1 else (" › ".join(path) if path else "—")


def build_context(kept: list[tuple]) -> str:
    """Чанки → контекст для промпта. В отличие от day-2/day-3, каждый фрагмент
    открывается строкой-паспортом [chunk_id | doc | section] — модели нужно
    на что ссылаться в sources/quotes."""
    parts = []
    for c, _, _ in kept:
        parts.append(f"[{c['id']} | {c['doc']} | {heading_of(c)}]\n{c['text']}")
    return "\n\n".join(parts)


# --------------------------------------------------------------------------- #
# Шаг 1: query rewrite (как в day-3)
# --------------------------------------------------------------------------- #


def rewrite_query(question: str, cache: dict[str, str]) -> str:
    if question in cache:
        return cache[question]
    raw = llm_call(
        [
            {"role": "system", "content": SYSTEM_REWRITE},
            {"role": "user", "content": question},
        ]
    )
    rewritten = " ".join(raw.split()).strip('"«»„“').strip()
    if not (3 <= len(rewritten) <= 300):  # защита от мусора — ищем исходным
        rewritten = question
    cache[question] = rewritten
    return rewritten


# --------------------------------------------------------------------------- #
# Шаг 3: LLM-реранкер + порог + топ-K2 (как в day-3)
# --------------------------------------------------------------------------- #


def _extract_json(raw: str):
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        return json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return None


def parse_scores(raw: str, n: int) -> list[float] | None:
    """Ответ судьи → список из n скоров 0–10. None = ответ не разобран."""
    data = _extract_json(raw)
    if not isinstance(data, dict) or not isinstance(data.get("scores"), list):
        return None
    out: list[float | None] = [None] * n
    for item in data["scores"]:
        if not isinstance(item, dict):
            continue
        try:
            i, s = int(item["id"]), float(item["score"])
        except (KeyError, TypeError, ValueError):
            continue
        if 1 <= i <= n:
            out[i - 1] = min(10.0, max(0.0, s))
    return None if any(s is None for s in out) else out


def rerank_scores(question: str, candidates: list[tuple[dict, float]]) -> list[float] | None:
    """Один батч-вызов LLM: скор релевантности 0–10 каждому кандидату.

    Возвращает список скоров, выровненный по candidates, или None — тогда
    вызывающий код честно деградирует до порядка по косинусу (без порога).
    """
    blocks = []
    for i, (c, _) in enumerate(candidates, start=1):
        text = " ".join(c["text"].split())[:400]
        blocks.append(f"[{i}] ({c['doc']} › {heading_of(c)})\n{text}")
    user = f"Вопрос: {question}\n\nОтрывки:\n\n" + "\n\n".join(blocks)
    messages = [
        {"role": "system", "content": SYSTEM_RERANK},
        {"role": "user", "content": user},
    ]
    for attempt in (1, 2):
        raw = llm_call(messages)
        scores = parse_scores(raw, len(candidates))
        if scores is not None:
            return scores
        print(f"    [реранкер: ответ не разобран, попытка {attempt}]")
    print("    [реранкер недоступен: отбор по косинусной близости без порога]")
    return None


def apply_stage2(
    candidates: list[tuple[dict, float]],
    scores: list[float] | None,
    k2: int,
    llm_threshold: float,
) -> tuple[list[tuple[dict, float, float | None]], bool]:
    """Реранк: сортировка по скору судьи → порог → топ-K2.

    Возвращает (kept, fallback). При scores=None — косинусный порядок без порога.
    """
    if scores is None:
        ranked = [(c, cos, None) for c, cos in candidates]
        return ranked[:k2], True
    ranked = [(c, cos, s) for (c, cos), s in zip(candidates, scores)]
    ranked.sort(key=lambda t: (-t[2], -t[1]))  # по скору судьи, ties — по косинусу
    kept = [item for item in ranked if item[2] >= llm_threshold][:k2]
    return kept, False


def build_hints(cands: list[tuple[dict, float]], scores: list[float] | None) -> list[str]:
    """Подсказки для отказа: секции кандидатов со скором ≥ 2 («по теме, но не
    отвечает») — вдруг вопрос просто неточно сформулирован."""
    if not scores:
        return []
    hints: list[str] = []
    for (c, _), s in sorted(zip(cands, scores), key=lambda t: -t[1]):
        if s < 2.0:
            break
        h = f"{c['doc']} › {heading_of(c)}"
        if h not in hints:
            hints.append(h)
        if len(hints) == 3:
            break
    return hints


# --------------------------------------------------------------------------- #
# Шаг 4: генерация со структурированным выходом (ответ + источники + цитаты)
# --------------------------------------------------------------------------- #


def parse_generation(raw: str) -> dict | None:
    """Ответ модели → {answer, sources, quotes}. None = JSON не разобран."""
    data = _extract_json(raw)
    if not isinstance(data, dict) or not isinstance(data.get("answer"), str):
        return None
    sources = data.get("sources")
    quotes = data.get("quotes")
    return {
        "answer": data["answer"].strip(),
        "sources": [s for s in sources if isinstance(s, dict)] if isinstance(sources, list) else [],
        "quotes": [q for q in quotes if isinstance(q, dict)] if isinstance(quotes, list) else [],
    }


def generate_structured(question: str, kept: list[tuple]) -> tuple[dict, str]:
    """Генерация ответа со строгим JSON-выходом.

    Возвращает (parsed, raw). При двух неудачных разборах — честный откат:
    весь текст ответа считается answer, sources/quotes пусты (parse_ok=False).
    """
    context = build_context(kept)
    messages = [
        {"role": "system", "content": SYSTEM_ANSWER},
        {
            "role": "user",
            "content": f"Фрагменты базы знаний:\n\n{context}\n\nВопрос: {question}",
        },
    ]
    raw = ""
    for attempt in (1, 2):
        raw = llm_call(messages)
        parsed = parse_generation(raw)
        if parsed is not None:
            return {**parsed, "parse_ok": True}, raw
        print(f"    [генерация: JSON не разобран, попытка {attempt}]")
    print("    [генерация: откат на простой текст без источников и цитат]")
    return {"answer": raw, "sources": [], "quotes": [], "parse_ok": False}, raw


# --------------------------------------------------------------------------- #
# Шаг 5: пост-валидация — детерминированные анти-галлюцинации
# --------------------------------------------------------------------------- #


def norm_text(s: str) -> str:
    """Нормализация для сравнения цитат: регистр, ё→е, кавычки/тире, пробелы.
    Пунктуацию (. , : ; ! ?) сохраняем — пересказ с чужой пунктуацией не должен
    засчитываться как дословная цитата."""
    s = s.lower().replace("ё", "е")
    s = re.sub(r"[«»„“”\"'`]", " ", s)
    s = re.sub(r"[—–−]", "-", s)
    return re.sub(r"\s+", " ", s).strip()


def fuzzy_contains(quote: str, text: str, threshold: float = QUOTE_MATCH_THRESHOLD) -> dict:
    """Есть ли цитата в тексте чанка: сначала дословное вхождение нормализованных
    строк, затем скользящее окно с difflib (мелкие LLM-искажения — замена слова,
    потерянный знак — не должны ронять валидную цитату, но пересказ не должен
    проходить)."""
    q, t = norm_text(quote), norm_text(text)
    if len(q) < 8:
        return {"valid": False, "ratio": 0.0, "reason": "цитата пустая или слишком короткая"}
    if q in t:
        return {"valid": True, "ratio": 1.0, "reason": "дословное вхождение"}
    best = 0.0
    step = max(1, len(q) // 8)
    win = len(q) + step  # окно чуть длиннее цитаты — допуск на вставки
    for start in range(0, max(1, len(t) - win + 1), step):
        ratio = difflib.SequenceMatcher(None, q, t[start : start + win]).ratio()
        if ratio > best:
            best = ratio
    if best >= threshold:
        return {"valid": True, "ratio": round(best, 3),
                "reason": f"нечёткое вхождение ({best:.2f} ≥ {threshold:.2f})"}
    return {"valid": False, "ratio": round(best, 3),
            "reason": f"текста цитаты нет в чанке (макс. совпадение {best:.2f} < {threshold:.2f})"}


def validate_sources(sources: list[dict], by_id: dict[str, dict]) -> list[dict]:
    """Источник валиден ⟺ chunk_id есть в контексте, doc совпадает с чанком,
    section совпадает с заголовком чанка (в любую сторону вложенности)."""
    out = []
    for s in sources:
        cid = str(s.get("chunk_id", "")).strip()
        doc = str(s.get("doc", "")).strip() or None
        section = str(s.get("section", "")).strip() or None
        chunk = by_id.get(cid)
        if chunk is None:
            out.append({"chunk_id": cid or "—", "doc": doc, "section": section,
                        "valid": False, "reason": "chunk_id отсутствует в контексте"})
            continue
        doc_ok = doc is None or doc == chunk["doc"]
        sec_ok = True
        if section:
            sec = norm_text(section)
            heads = [norm_text(h) for h in (chunk.get("heading_path") or []) if h]
            sec_ok = any(h in sec or sec in h for h in heads)
        valid = doc_ok and sec_ok
        reason = ("ок" if valid
                  else "документ не совпадает с чанком" if not doc_ok
                  else "раздел не совпадает с чанком")
        out.append({"chunk_id": cid, "doc": doc or chunk["doc"], "section": section,
                    "valid": valid, "reason": reason})
    return out


def validate_quotes(quotes: list[dict], by_id: dict[str, dict]) -> list[dict]:
    """Цитата валидна ⟺ chunk_id есть в контексте И текст цитаты реально
    содержится в этом чанке (дословно или с мелкими искажениями)."""
    out = []
    for q in quotes:
        text = str(q.get("text", "")).strip()
        cid = str(q.get("chunk_id", "")).strip()
        chunk = by_id.get(cid)
        if chunk is None:
            out.append({"chunk_id": cid or "—", "text": text[:300],
                        "valid": False, "ratio": 0.0,
                        "reason": "chunk_id отсутствует в контексте"})
            continue
        m = fuzzy_contains(text, chunk["text"])
        out.append({"chunk_id": cid, "text": text[:300], **m})
    return out


# --------------------------------------------------------------------------- #
# Шаг 6: faithfulness-судья — соответствие смысла ответа цитатам
# --------------------------------------------------------------------------- #


def faithfulness_verdict(
    question: str, answer: str, valid_quotes: list[dict], by_id: dict[str, dict]
) -> dict:
    """Один LLM-вызов: supported / partial / unsupported / no_quotes / unknown."""
    if not answer.strip():
        return {"verdict": "no_answer", "reason": "ответ пуст"}
    if not valid_quotes:
        return {"verdict": "no_quotes",
                "reason": "валидных цитат нет — соответствие проверять нечем"}
    blocks = []
    for q in valid_quotes:
        c = by_id[q["chunk_id"]]
        blocks.append(f"({c['doc']} › {heading_of(c)})\n«{q['text']}»")
    messages = [
        {"role": "system", "content": SYSTEM_FAITH},
        {
            "role": "user",
            "content": (f"Вопрос: {question}\n\nОтвет ассистента:\n{answer}\n\n"
                        "Цитаты из базы знаний:\n\n" + "\n\n".join(blocks)),
        },
    ]
    for attempt in (1, 2):
        raw = llm_call(messages)
        data = _extract_json(raw)
        verdict = str((data or {}).get("verdict", "")).strip().lower()
        if isinstance(data, dict) and verdict in VERDICTS:
            return {"verdict": verdict, "reason": str(data.get("reason", ""))[:200]}
        print(f"    [судья: ответ не разобран, попытка {attempt}]")
    return {"verdict": "unknown", "reason": "ответ судьи не разобран"}


# --------------------------------------------------------------------------- #
# Авто-оценка фактов (как в day-2/day-3)
# --------------------------------------------------------------------------- #


def check_facts(answer: str, expected_facts: list[dict]) -> list[dict]:
    low = answer.lower()
    return [
        {"fact": f["fact"], "ok": any(v.lower() in low for v in f["any_of"])}
        for f in expected_facts
    ]


# --------------------------------------------------------------------------- #
# Полный прогон одного вопроса
# --------------------------------------------------------------------------- #


class Runtime:
    def __init__(self, index: dict, embedder: QueryEmbedder, k1: int, k2: int, threshold: float):
        self.index = index
        self.embedder = embedder
        self.k1, self.k2, self.threshold = k1, k2, threshold
        self.rewrite_cache: dict[str, str] = {}


def run_case(case: dict, rtx: Runtime) -> dict:
    question = case["question"]

    # [1] rewrite → [2] поиск → [3] реранк + порог + топ-K2
    rewritten = rewrite_query(question, rtx.rewrite_cache)
    cands = vector_search(rtx.index["chunks"], rtx.embedder.embed_query(rewritten), rtx.k1)
    scores = rerank_scores(question, cands)
    kept, fallback = apply_stage2(cands, scores, rtx.k2, rtx.threshold)
    hints = build_hints(cands, scores)
    by_id = {c["id"]: c for c, _, _ in kept}

    def cand_meta(item, score):
        c, cos = item[0], item[1]
        return {"chunk_id": c["id"], "doc": c["doc"], "heading": heading_of(c),
                "cos": round(cos, 3),
                "llm_score": score if score is None else round(score, 1)}

    result = {
        "id": case["id"],
        "kind": case["kind"],
        "question": question,
        "rewritten": rewritten,
        "n_candidates": len(cands),
        "candidates": [cand_meta(item, s) for item, s in zip(
            cands, scores if scores else [None] * len(cands))],
        "max_llm_score": max(scores) if scores else None,
        "n_kept": len(kept),
        "kept": [cand_meta((c, cos), s) for c, cos, s in kept],
        "fallback": fallback,
        "threshold": rtx.threshold,
        "hints": hints,
    }

    # Порог релевантности не прошёл никто: контекст пуст → «не знаю» + уточнение,
    # генерация не вызывается вовсе (анти-галлюцинация на уровне поиска).
    if not kept:
        final = (REFUSAL_EMPTY_CTX_WITH_HINTS.format(hints=" · ".join(hints))
                 if hints else REFUSAL_EMPTY_CTX_PLAIN)
        result.update({
            "final_answer": final,
            "refusal": True,
            "refusal_kind": "empty_context",
            "clarification": any(m in final.lower() for m in CLARIFY_MARKERS),
            "sources": [],
            "quotes": [],
            "faithfulness": None,
            "generation": None,
        })
        if case["kind"] == "corpus":
            facts = check_facts(final, case["expected_facts"])
            result["facts"] = facts
            result["facts_ok"] = sum(f["ok"] for f in facts)
        return result

    # [4] генерация → [5] валидация → [6] судья
    parsed, raw = generate_structured(question, kept)
    sources_v = validate_sources(parsed["sources"], by_id)
    quotes_v = validate_quotes(parsed["quotes"], by_id)
    valid_quotes = [q for q in quotes_v if q["valid"]]
    faith = faithfulness_verdict(question, parsed["answer"], valid_quotes, by_id)

    final, refusal, refusal_kind = parsed["answer"], False, None
    if not parsed["answer"]:  # модель сама сказала «в базе нет»
        final = (REFUSAL_EMPTY_CTX_WITH_HINTS.format(hints=" · ".join(hints))
                 if hints else REFUSAL_EMPTY_CTX_PLAIN)
        refusal, refusal_kind = True, "model_no_answer"
    elif faith["verdict"] == "unsupported":  # смысл не выводится из цитат
        final = REFUSAL_UNFAITHFUL
        refusal, refusal_kind = True, "unfaithful"

    result.update({
        "generation": {"parsed": parsed, "raw": raw},
        "sources": sources_v,
        "quotes": quotes_v,
        "faithfulness": faith,
        "answer_original": parsed["answer"] if refusal else None,
        "final_answer": final,
        "refusal": refusal,
        "refusal_kind": refusal_kind,
        "clarification": any(m in final.lower() for m in CLARIFY_MARKERS),
    })
    if case["kind"] == "corpus":
        facts = check_facts(final, case["expected_facts"])
        result["facts"] = facts
        result["facts_ok"] = sum(f["ok"] for f in facts)
        result["facts_all"] = all(f["ok"] for f in facts)
    return result


# --------------------------------------------------------------------------- #
# Датасет
# --------------------------------------------------------------------------- #


def load_dataset() -> list[dict]:
    if not QUESTIONS_FILE.exists():
        sys.exit(f"[ОШИБКА] Файл вопросов не найден: {QUESTIONS_FILE}")
    data = json.loads(QUESTIONS_FILE.read_text(encoding="utf-8"))
    return ([{**c, "kind": "corpus"} for c in data["corpus"]]
            + [{**c, "kind": "off_corpus"} for c in data["off_corpus"]])


# --------------------------------------------------------------------------- #
# Вывод
# --------------------------------------------------------------------------- #


def short(text: str, limit: int = 110) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def print_case(r: dict) -> None:
    print(f"\n  {r['id']} ({'корпус' if r['kind'] == 'corpus' else 'вне базы'}): "
          f"«{short(r['question'])}»")
    print(f"      rewrite → «{short(r['rewritten'], 80)}»")
    if r["n_kept"] == 0:
        ms = r["max_llm_score"] if r["max_llm_score"] is not None else "—"
        print(f"      отбор 0/{r['n_candidates']} (макс. скор судьи {ms} < "
              f"порога {r['threshold']:g}) → генерация не вызывается")
        print(f"      ответ: «{short(r['final_answer'], 160)}»")
        print(f"      отказ: {'да' if r['refusal'] else 'НЕТ'} · "
              f"просит уточнить: {'да' if r['clarification'] else 'НЕТ'}")
        return

    kept_line = ", ".join(f"{k['chunk_id']} ({k['llm_score']:g})" for k in r["kept"])
    print(f"      отбор {r['n_kept']}/{r['n_candidates']} в контекст: {kept_line}")
    sv = sum(s["valid"] for s in r["sources"])
    qv = sum(q["valid"] for q in r["quotes"])
    line = (f"      ответ: источники {sv}/{len(r['sources'])} · цитаты "
            f"{qv}/{len(r['quotes'])} · смысл: {r['faithfulness']['verdict']}")
    if r["kind"] == "corpus":
        line += f" · факты {r['facts_ok']}/{len(r['facts'])}"
    if r.get("fallback"):
        line += " [реранк недоступен]"
    print(line)
    for s in r["sources"]:
        mark = "✓" if s["valid"] else "✗"
        tail = "" if s["valid"] else f" — {s['reason']}"
        print(f"        источник {mark} {s['doc']} › {s['section']} "
              f"[{s['chunk_id']}]{tail}")
    for q in r["quotes"]:
        mark = "✓" if q["valid"] else "✗"
        tail = (f" (совпадение {q['ratio']:.2f})" if q["valid"]
                else f" — {q['reason']}")
        print(f"        цитата   {mark} «{short(q['text'], 88)}» [{q['chunk_id']}]{tail}")
    if r["refusal"]:
        print(f"        → ответ ОТВЕРГНУТ ({r['refusal_kind']}), финал: "
              f"«{short(r['final_answer'], 120)}»")
        print(f"        просит уточнить: {'да' if r['clarification'] else 'НЕТ'}")


def summarize(cases: list[dict], results: list[dict]) -> dict:
    corpus = [r for r in results if r["kind"] == "corpus"]
    off = [r for r in results if r["kind"] == "off_corpus"]
    n_facts = sum(len(c["expected_facts"]) for c in cases if c["kind"] == "corpus")

    def verdict(v: str) -> int:
        return sum(1 for r in corpus if r["faithfulness"] and r["faithfulness"]["verdict"] == v)

    sources_total = sum(len(r["sources"]) for r in corpus)
    sources_valid = sum(sum(s["valid"] for s in r["sources"]) for r in corpus)
    quotes_total = sum(len(r["quotes"]) for r in corpus)
    quotes_valid = sum(sum(q["valid"] for q in r["quotes"]) for r in corpus)

    return {
        "n_corpus": len(corpus), "n_off": len(off), "n_facts": n_facts,
        "facts": sum(r["facts_ok"] for r in corpus),
        "facts_all": sum(r.get("facts_all", False) for r in corpus),
        "has_source": sum(1 for r in corpus
                          if any(s["valid"] for s in r["sources"])),
        "all_sources_valid": sum(1 for r in corpus
                                 if r["sources"] and all(s["valid"] for s in r["sources"])),
        "sources_returned": sources_total,
        "sources_valid": sources_valid,
        "sources_hallucinated": sources_total - sources_valid,
        "has_quote": sum(1 for r in corpus
                         if any(q["valid"] for q in r["quotes"])),
        "all_quotes_valid": sum(1 for r in corpus
                                if r["quotes"] and all(q["valid"] for q in r["quotes"])),
        "quotes_returned": quotes_total,
        "quotes_valid": quotes_valid,
        "quotes_hallucinated": quotes_total - quotes_valid,
        "supported": verdict("supported"),
        "partial": verdict("partial"),
        "unsupported": verdict("unsupported"),
        "no_quotes": verdict("no_quotes"),
        "answers_replaced": sum(r["refusal"] for r in corpus),
        "off_empty": sum(1 for r in off if r["n_kept"] == 0),
        "off_refusal": sum(r["refusal"] for r in off),
        "off_clarify": sum(r["clarification"] for r in off),
    }


def print_summary(s: dict) -> None:
    n, no, nf = s["n_corpus"], s["n_off"], s["n_facts"]
    print("\n" + "=" * 78)
    print("[3] Итог — источники, цитаты, соответствие смысла")
    print("=" * 78)
    print(f"    корпус: {n} вопросов ({nf} фактов) · вне базы: {no} вопросов\n")
    rows = [
        ("фактов подтверждено (авточек day-2)", f"{s['facts']}/{nf}"),
        ("вопросов со всеми фактами", f"{s['facts_all']}/{n}"),
        ("есть ≥1 валидный источник", f"{s['has_source']}/{n}"),
        ("все источники валидны",
         f"{s['all_sources_valid']}/{n} · ссылок {s['sources_returned']}, "
         f"валидных {s['sources_valid']}, ПОДДЕЛОК {s['sources_hallucinated']}"),
        ("есть ≥1 валидная цитата", f"{s['has_quote']}/{n}"),
        ("все цитаты валидны (дословно из чанка)",
         f"{s['all_quotes_valid']}/{n} · цитат {s['quotes_returned']}, "
         f"валидных {s['quotes_valid']}, ГАЛЛЮЦИНАЦИЙ {s['quotes_hallucinated']}"),
        ("смысл: supported / partial / unsupported",
         f"{s['supported']}/{s['partial']}/{s['unsupported']}"
         + (f" · без цитат {s['no_quotes']}" if s["no_quotes"] else "")),
        ("ответов заменено на «не знаю» (корпус)", f"{s['answers_replaced']}/{n}"),
        ("вне базы: пустой контекст", f"{s['off_empty']}/{no}"),
        ("вне базы: честный отказ «не знаю»", f"{s['off_refusal']}/{no}"),
        ("вне базы: просит уточнить вопрос", f"{s['off_clarify']}/{no}"),
    ]
    for label, value in rows:
        print(f"  {label:<40} {value}")


# --------------------------------------------------------------------------- #
# Демо одного вопроса (--question): полный след пайплайна
# --------------------------------------------------------------------------- #


def demo(question: str, rtx: Runtime) -> None:
    print("\n" + "=" * 78)
    print(f"[2] Демо: «{question}»")
    print("=" * 78)

    rewritten = rewrite_query(question, rtx.rewrite_cache)
    print(f"\n  [1] rewrite → «{rewritten}»")
    cands = vector_search(rtx.index["chunks"], rtx.embedder.embed_query(rewritten), rtx.k1)
    scores = rerank_scores(question, cands)
    kept, fallback = apply_stage2(cands, scores, rtx.k2, rtx.threshold)
    kept_ids = {c["id"] for c, _, _ in kept}
    print(f"\n  [2–3] кандидаты топ-{len(cands)} → реранк + порог {rtx.threshold:g} → "
          f"топ-{len(kept)} в контекст:")
    for (c, cos), s in zip(cands, scores if scores else [None] * len(cands)):
        if c["id"] in kept_ids:
            verdict = "В КОНТЕКСТ"
        elif s is None:
            verdict = "не прошёл"
        elif s >= rtx.threshold:
            verdict = (f"релевантен (скор {s:g} ≥ {rtx.threshold:g}), "
                       f"но за пределами топ-{rtx.k2}")
        else:
            verdict = f"отсечён (скор {s:g} < {rtx.threshold:g})"
        print(f"    [{cos:.3f}] (скор {s if s is not None else '—'}) {c['id']} · "
              f"{c['doc']} · {heading_of(c)} — {verdict}")
    if fallback:
        print("    [реранкер недоступен — отбор по косинусной близости]")

    case = {"id": "DEMO", "kind": "demo", "question": question}
    r = run_case(case, rtx)

    if r["n_kept"] == 0:
        print("\n  [4–6] контекст пуст → генерация не вызывается")
        print(f"    финальный ответ: «{r['final_answer']}»")
        return

    print("\n  [4] ответ модели (структурированный JSON):")
    print("    " + json.dumps(r["generation"]["parsed"], ensure_ascii=False,
                              indent=2).replace("\n", "\n    "))
    by_id = {c["id"]: c for c, _, _ in kept}
    print("\n  [5] пост-валидация (анти-галлюцинации):")
    for s in r["sources"]:
        print(f"    источник {'✓' if s['valid'] else '✗'} [{s['chunk_id']}] {s['doc']} "
              f"› {s['section']} — {s['reason']}")
    for q in r["quotes"]:
        print(f"    цитата   {'✓' if q['valid'] else '✗'} [{q['chunk_id']}] "
              f"(совпадение {q['ratio']:.2f}) «{short(q['text'], 90)}»"
              + ("" if q["valid"] else f" — {q['reason']}"))
    f = r["faithfulness"]
    print(f"\n  [6] faithfulness-судья: {f['verdict']} — {f['reason']}")
    if r["refusal"]:
        print(f"    → ответ отвергнут ({r['refusal_kind']}): «{short(r['final_answer'], 160)}»")
    else:
        print(f"\n  ФИНАЛЬНЫЙ ОТВЕТ:\n    {r['final_answer'].replace(chr(10), chr(10) + '    ')}")
        print("    Источники: " + "; ".join(
            f"{s['doc']} › {s['section']} [{s['chunk_id']}]" for s in r["sources"] if s["valid"]))


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Week 5 Day 4 — цитаты, источники и анти-галлюцинации в RAG"
    )
    parser.add_argument("--question", help="демо пайплайна на одном вопросе")
    parser.add_argument("--index", default=str(DEFAULT_INDEX), help="путь к индексу day-1")
    parser.add_argument("--k1", type=int, default=K1_DEFAULT, help="top-K ДО отбора (кандидаты)")
    parser.add_argument("--k2", type=int, default=K2_DEFAULT, help="top-K ПОСЛЕ отбора (в контекст)")
    parser.add_argument("--threshold", type=float, default=LLM_THRESHOLD_DEFAULT,
                        help="порог LLM-скора релевантности (0–10)")
    args = parser.parse_args()

    load_dotenv(BASE_DIR / ".env")

    print("Week 5 · Day 4 — Цитаты, источники и анти-галлюцинации "
          "(структурированный RAG-ответ + валидация + «не знаю» ниже порога)")

    print("\n" + "=" * 78)
    print("[1] Индекс, эмбеддинги, LLM и конфигурация")
    print("=" * 78)
    index = load_index(Path(args.index))
    emb_meta = index["embedding"]
    print(f"    индекс: {args.index}")
    print(f"    чанков: {len(index['chunks'])} · стратегия «{index['strategy']}» · "
          f"документов: {len(index['documents'])}")
    print(f"    эмбеддинги запросов: {emb_meta['provider']} · {emb_meta['model']} · "
          f"{emb_meta['dim']} измерений")
    print(f"    LLM: {os.getenv('ZAI_MODEL', ZAI_MODEL_DEFAULT)} @ "
          f"{os.getenv('ZAI_CHAT_URL', ZAI_CHAT_URL_DEFAULT)} · temperature={TEMPERATURE}")
    print(f"    top-K: {args.k1} до отбора → {args.k2} после · "
          f"порог релевантности (LLM-скор): {args.threshold:g} · "
          f"порог совпадения цитаты: {QUOTE_MATCH_THRESHOLD:.2f}")

    embedder = QueryEmbedder(emb_meta["model"])
    rtx = Runtime(index, embedder, args.k1, args.k2, args.threshold)

    if args.question:
        demo(args.question, rtx)
        print("\nГотово.")
        return

    cases = load_dataset()
    print(f"    вопросы: {sum(1 for c in cases if c['kind'] == 'corpus')} корпусных (day-2) + "
          f"{sum(1 for c in cases if c['kind'] == 'off_corpus')} вне-корпусных (day-3)")

    print("\n" + "=" * 78)
    print("[2] Контрольные вопросы: ответ + источники + цитаты → валидация → судья")
    print("=" * 78)
    results = [run_case(case, rtx) for case in cases]
    for r in results:
        print_case(r)

    summary = summarize(cases, results)
    print_summary(summary)

    RESULTS_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = RESULTS_DIR / f"eval_{stamp}.json"
    out.write_text(
        json.dumps(
            {
                "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "config": {
                    "index": {"strategy": index["strategy"], "embedding": index["embedding"]},
                    "model": os.getenv("ZAI_MODEL", ZAI_MODEL_DEFAULT),
                    "temperature": TEMPERATURE,
                    "k1": rtx.k1, "k2": rtx.k2,
                    "llm_threshold": rtx.threshold,
                    "quote_match_threshold": QUOTE_MATCH_THRESHOLD,
                },
                "summary": summary,
                "results": results,
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    print(f"\n  Результаты сохранены: {out}")
    print("\nГотово.")


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    main()
