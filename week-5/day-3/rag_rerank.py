"""
Week 5 · Day 3 — Reranking & Filtering.

Задание: добавить после векторного поиска второй этап — реранкер и/или фильтр
релевантности; настроить порог отсечения и top-K до/после отбора; сравнить
качество RAG без фильтра/rewrite и с улучшениями.

Пайплайн улучшенного режима (full):
  вопрос ─► [1] query rewrite (LLM: разговорный вопрос → поисковый запрос;
              rewrite используется ТОЛЬКО для поиска, отвечаем на исходный вопрос)
         ─► [2] векторный поиск → топ-K1 кандидатов (K1 = 8)
         ─► [3] второй этап: LLM-judge скорит релевантность каждого кандидата
              0–10 (один батч-вызов, строгий JSON) → порог отсечения → топ-K2
              (K2 = 3); ничего не прошло → контекст пуст → честный отказ
         ─► [4] контекст [1..K2] + исходный вопрос → glm-4.6 → ответ с источником.

Сравнение режимов (2×2: вклад rewrite и второго этапа по отдельности и вместе):
  baseline — без всего: топ-K1 кандидатов сразу в контекст (реплика «без фильтра»);
  rewrite  — только query rewrite, отбора нет;
  rerank   — только второй этап (реранк + фильтр + топ-K2);
  full     — rewrite + второй этап.

Порог настраивается sweep-ом (без генерации ответов): для каждого порога
считается (а) доля корпусных вопросов, где эталонный документ остался в отборе,
(б) доля вне-корпусных вопросов с пустым контекстом. Порог выбирается на
«изломе»: максимум сохранённых эталонов при максимуме отфильтрованного шума.

Оценка: 10 вопросов day-2 + 5 «сложных» (разговорные формулировки без ключевых
слов — поле деятельности rewrite) + 3 вне-корпусных (в базе их нет; правильное
поведение — пустой контекст и честный отказ). Метрики: факты, цитирование,
hit@1/hit@K/секция ПОСЛЕ отбора, честный отказ, размер контекста.

Запуск:
    python rag_rerank.py                     # sweep порога + полный прогон 4 режимов
    python rag_rerank.py --sweep-only        # только настройка порогов (без ответов)
    python rag_rerank.py --question "..."    # демо пайплайна на одном вопросе
    python rag_rerank.py --k1 10 --k2 4      # другие top-K до/после отбора
    python rag_rerank.py --threshold 6       # свой порог LLM-скора (sweep не выбирает)
    python rag_rerank.py --stage2 threshold  # второй этап = чистый косинусный фильтр
    python rag_rerank.py --modes baseline,full

Индекс берётся из day-1 (по умолчанию ../day-1/index/structured.json), вопросы
day-2 — из ../day-2/eval_questions.json. Ключ ZAI_API_KEY хранится в .env
(gitignored), в git не попадает.
"""

from __future__ import annotations

import argparse
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

# Тихий режим кэша HuggingFace на Windows (как в day-1/day-2).
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_INDEX = BASE_DIR.parent / "day-1" / "index" / "structured.json"
DAY2_QUESTIONS = BASE_DIR.parent / "day-2" / "eval_questions.json"
EXTRA_QUESTIONS = BASE_DIR / "eval_extra.json"
RESULTS_DIR = BASE_DIR / "results"

# OpenAI-совместимый coding-эндпоинт Z.ai (тот же, что в day-2).
ZAI_CHAT_URL_DEFAULT = "https://api.z.ai/api/coding/paas/v4/chat/completions"
ZAI_MODEL_DEFAULT = "glm-4.6"
TEMPERATURE = 0.0
REQUEST_TIMEOUT = 120

K1_DEFAULT = 8            # top-K ДО отбора: сколько кандидатов достаёт поиск
K2_DEFAULT = 3            # top-K ПОСЛЕ отбора: сколько чанков попадает в контекст
LLM_THRESHOLD_DEFAULT = 6     # порог LLM-скора релевантности (0–10), уточняется sweep-ом
COS_THRESHOLD_DEFAULT = 0.55  # порог косинусной близости (для --stage2 threshold)
LLM_SWEEP = tuple(range(2, 9))          # сетка порогов LLM-скора для sweep
COS_SWEEP = tuple(round(x, 2) for x in np.arange(0.40, 0.71, 0.05))

REFUSAL_TEXT = (
    "В базе знаний курса нет информации по этому вопросу: релевантных "
    "фрагментов не найдено."
)

# System-промпты. SYSTEM_RAG — тот же, что в day-2 (честное сравнение дней).
SYSTEM_RAG = (
    "Ты — ассистент с доступом к базе знаний курса. Отвечай по-русски, кратко "
    "(2–6 предложений). Пользуйся ТОЛЬКО приведённым контекстом: если ответа в "
    "нём нет — честно скажи об этом. В конце ответа обязательно укажи источник "
    "в формате [имя_файла › раздел]."
)
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

MODES = {  # порядок колонок в итоговой таблице
    "baseline": {"rewrite": False, "stage2": False, "label": "без фильтра/rewrite"},
    "rewrite": {"rewrite": True, "stage2": False, "label": "query rewrite"},
    "rerank": {"rewrite": False, "stage2": True, "label": "реранк + фильтр"},
    "full": {"rewrite": True, "stage2": True, "label": "rewrite + реранк + фильтр"},
}

REFUSAL_MARKERS = (
    "не содержится", "нет в базе", "нет информации", "не найдено", "не нашёл",
    "не нашлось", "отсутствует", "не покрывает", "не относится", "нет данных",
    "не представлены", "ничего нет", "нет релевантн", "не удалось найти",
    "нет сведений", "ничего релевантного", "не имеется",
)


# --------------------------------------------------------------------------- #
# Ошибки LLM-вызова (как в day-2: retry только на сетевых/серверных сбоях)
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
# Индекс и поиск (как в day-2)
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


def build_context(hits: list[tuple[dict, float] | dict]) -> str:
    """Чанки → нумерованный контекст для промпта (как в day-2)."""
    parts = []
    for i, item in enumerate(hits, start=1):
        c = item[0] if isinstance(item, tuple) else item
        parts.append(f"[{i}] ({c['doc']})\n{c['text']}")
    return "\n\n".join(parts)


# --------------------------------------------------------------------------- #
# Шаг 1: query rewrite
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
# Шаг 3: второй этап — LLM-реранкер и/или фильтр релевантности
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
    stage2: str,
    candidates: list[tuple[dict, float]],
    scores: list[float] | None,
    k2: int,
    llm_threshold: float,
    cos_threshold: float,
) -> tuple[list[tuple[dict, float, float | None]], list[tuple[dict, float, float | None]], bool]:
    """Второй этап: (пере)ранжирование + фильтр по порогу + топ-K2.

    Возвращает (kept, dropped, fallback). Каждый элемент — (chunk, cosine,
    llm_score|None). fallback=True — реранкер не ответил, ранжируем по косинусу.
    """
    if stage2 == "threshold":  # чистый фильтр: порог по косинусной близости
        ranked = [(c, cos, None) for c, cos in candidates]
        kept, dropped = [], []
        for item in ranked:
            if len(kept) < k2 and item[1] >= cos_threshold:
                kept.append(item)
            else:
                dropped.append(item)
        return kept, dropped, False

    # stage2 == "rerank": LLM-скор → сортировка → порог → топ-K2
    if scores is None:  # деградация: без скоров судьи — косинусный порядок без порога
        ranked = [(c, cos, None) for c, cos in candidates]
        return ranked[:k2], ranked[k2:], True
    ranked = [(c, cos, s) for (c, cos), s in zip(candidates, scores)]
    ranked.sort(key=lambda t: (-t[2], -t[1]))  # по скору судьи, ties — по косинусу
    kept, dropped = [], []
    for item in ranked:
        if len(kept) < k2 and item[2] >= llm_threshold:
            kept.append(item)
        else:
            dropped.append(item)
    return kept, dropped, False


# --------------------------------------------------------------------------- #
# Шаг 4: ответ
# --------------------------------------------------------------------------- #


def answer_question(question: str, kept: list[tuple]) -> tuple[str, bool]:
    """Ответ по контексту. Пустой контекст → детерминированный честный отказ
    (LLM не вызывается — это и есть выигрыш фильтра)."""
    if not kept:
        return REFUSAL_TEXT, True
    context = build_context(kept)
    answer = llm_call(
        [
            {"role": "system", "content": SYSTEM_RAG},
            {
                "role": "user",
                "content": f"Контекст из базы знаний:\n\n{context}\n\nВопрос: {question}",
            },
        ]
    )
    return answer, False


# --------------------------------------------------------------------------- #
# Авто-оценка (факты/цитирование — как в day-2; отказ — новое)
# --------------------------------------------------------------------------- #


def check_facts(answer: str, expected_facts: list[dict]) -> list[dict]:
    low = answer.lower()
    return [
        {"fact": f["fact"], "ok": any(v.lower() in low for v in f["any_of"])}
        for f in expected_facts
    ]


def cites_source(answer: str, doc: str) -> bool:
    low = answer.lower()
    stem = doc[:-3] if doc.endswith(".md") else doc
    return doc.lower() in low or stem.lower() in low


def cites_any_doc(answer: str, doc_names: list[str]) -> bool:
    low = answer.lower()
    return any(d[:-3].lower() in low for d in doc_names) or ".md" in low or "›" in low


def is_refusal(answer: str, doc_names: list[str]) -> bool:
    """Честный отказ = сказал «в базе нет» И не сослался ни на один документ."""
    low = answer.lower()
    return any(m in low for m in REFUSAL_MARKERS) and not cites_any_doc(answer, doc_names)


def selection_metrics(kept: list[tuple], source: dict) -> dict:
    """Качество поискового звена ПОСЛЕ второго этапа (top-1 / top-K / секция)."""
    docs = [item[0]["doc"] for item in kept]
    top1_path = kept[0][0].get("heading_path") or [] if kept else []
    return {
        "hit1": bool(docs) and docs[0] == source["doc"],
        "hitk": source["doc"] in docs,
        "section": bool(top1_path and set(source["sections"]) & set(top1_path)),
        "top1_doc": docs[0] if docs else "—",
        "top1_heading": heading_of(kept[0][0]) if kept else "—",
        "top1_cos": round(kept[0][1], 3) if kept else 0.0,
        "top1_score": kept[0][2] if kept and kept[0][2] is not None else None,
    }


# --------------------------------------------------------------------------- #
# Рантайм с кэшами (rewrite/кандидаты/скоры считаются один раз на вопрос)
# --------------------------------------------------------------------------- #


class Runtime:
    def __init__(self, index: dict, embedder: QueryEmbedder, k1: int, k2: int,
                 stage2: str, llm_threshold: float, cos_threshold: float):
        self.index = index
        self.embedder = embedder
        self.k1, self.k2 = k1, k2
        self.stage2 = stage2
        self.llm_threshold = llm_threshold
        self.cos_threshold = cos_threshold
        self.rewrite_cache: dict[str, str] = {}
        self.cand_cache: dict[tuple[str, str], list[tuple[dict, float]]] = {}
        self.score_cache: dict[tuple[str, str], list[float] | None] = {}
        self.doc_names = [d["name"] for d in index["documents"]]

    def query_text(self, case: dict, qt: str) -> str:
        return rewrite_query(case["question"], self.rewrite_cache) if qt == "rew" \
            else case["question"]

    def candidates(self, case: dict, qt: str) -> list[tuple[dict, float]]:
        key = (case["id"], qt)
        if key not in self.cand_cache:
            self.cand_cache[key] = vector_search(
                self.index["chunks"], self.embedder.embed_query(self.query_text(case, qt)), self.k1
            )
        return self.cand_cache[key]

    def llm_scores(self, case: dict, qt: str) -> list[float] | None:
        """Скоры судьи всегда считаются относительно ИСХОДНОГО вопроса:
        rewrite помогает достать кандидатов, а судить о релевантности нужно
        по настоящей информационной потребности."""
        key = (case["id"], qt)
        if key not in self.score_cache:
            self.score_cache[key] = rerank_scores(case["question"], self.candidates(case, qt))
        return self.score_cache[key]


def run_mode(case: dict, mode: str, rtx: Runtime) -> dict:
    """Полный прогон одного вопроса в одном режиме."""
    cfg = MODES[mode]
    qt = "rew" if cfg["rewrite"] else "orig"
    cands = rtx.candidates(case, qt)
    if cfg["stage2"]:
        scores = rtx.llm_scores(case, qt)
        kept, dropped, fallback = apply_stage2(
            rtx.stage2, cands, scores, rtx.k2, rtx.llm_threshold, rtx.cos_threshold
        )
    else:  # без отбора: весь топ-K1 сразу в контекст
        kept = [(c, cos, None) for c, cos in cands]
        dropped, fallback = [], False

    answer, canned = answer_question(case["question"], kept)

    out = {
        "mode": mode,
        "query_type": qt,
        "rewritten": rtx.query_text(case, "rew") if cfg["rewrite"] else None,
        "n_candidates": len(cands),
        "n_kept": len(kept),
        "kept": [
            {"doc": c["doc"], "heading": heading_of(c), "cos": round(cos, 3),
             "llm_score": (s if s is None else round(s, 1))}
            for c, cos, s in kept
        ],
        "dropped": [
            {"doc": c["doc"], "heading": heading_of(c), "cos": round(cos, 3),
             "llm_score": (s if s is None else round(s, 1))}
            for c, cos, s in dropped
        ],
        "fallback": fallback,
        "context_chars": sum(len(c[0]["text"]) for c in kept),
        "answer": answer,
        "canned_refusal": canned,
    }
    if case["kind"] == "off_corpus":
        out["refusal"] = canned or is_refusal(answer, rtx.doc_names)
    else:
        facts = check_facts(answer, case["expected_facts"])
        out["facts"] = facts
        out["facts_ok"] = sum(f["ok"] for f in facts)
        out["facts_all"] = all(f["ok"] for f in facts)
        out["cites"] = cites_source(answer, case["source"]["doc"])
        out["search"] = selection_metrics(kept, case["source"])
    return out


# --------------------------------------------------------------------------- #
# Sweep порогов: настройка БЕЗ генерации ответов
# --------------------------------------------------------------------------- #


def materialize(rtx: Runtime, cases: list[dict]) -> None:
    """Прогрев кэшей: rewrite + кандидаты + скоры судьи для обоих типов запроса.
    Дальше sweep и все режимы переиспользуют эти данные — LLM-вызовы не повторяются."""
    for case in cases:
        for qt in ("orig", "rew"):
            rtx.candidates(case, qt)
            rtx.llm_scores(case, qt)


def kept_for_threshold(rtx: Runtime, case: dict, qt: str, kind: str, t: float) -> list[tuple]:
    cands = rtx.candidates(case, qt)
    if kind == "cos":
        kept, _, _ = apply_stage2("threshold", cands, None, rtx.k2, t, t)
    else:
        scores = rtx.score_cache[(case["id"], qt)]
        kept, _, _ = apply_stage2("rerank", cands, scores, rtx.k2, t, t)
    return kept


def sweep_table(rtx: Runtime, corpus: list[dict], off: list[dict], qt: str,
                kind: str, thresholds) -> list[dict]:
    rows = []
    for t in thresholds:
        gold = sum(
            1 for case in corpus
            if any(item[0]["doc"] == case["source"]["doc"]
                   for item in kept_for_threshold(rtx, case, qt, kind, t))
        )
        empty = sum(1 for case in off if not kept_for_threshold(rtx, case, qt, kind, t))
        rows.append({"threshold": t, "gold_kept": gold, "off_empty": empty})
    return rows


def choose_threshold(rows: list[dict]) -> float:
    """Максимум сохранённых эталонов → максимум отфильтрованного шума →
    меньший порог (меньше риска отсечь гранично релевантное)."""
    best = max(rows, key=lambda r: (r["gold_kept"], r["off_empty"], -float(r["threshold"])))
    return float(best["threshold"])


def print_sweep(title: str, rows: list[dict], n_corpus: int, n_off: int) -> None:
    print(f"\n  {title}")
    head = "  ".join(f"{r['threshold']:>4}" for r in rows)
    print(f"    порог:                {head}")
    print(f"    эталон в отборе:      " + "  ".join(
        f"{r['gold_kept']:>2}/{n_corpus}" for r in rows))
    print(f"    пустой контекст:      " + "  ".join(
        f"{r['off_empty']:>2}/{n_off}" for r in rows))


def run_sweep(rtx: Runtime, cases: list[dict]) -> dict:
    corpus = [c for c in cases if c["kind"] != "off_corpus"]
    off = [c for c in cases if c["kind"] == "off_corpus"]
    print("    Считаем скоры судьи для всех вопросов (переписанные и исходные запросы)...")
    materialize(rtx, cases)

    result = {}
    for qt, qt_label in (("orig", "исходный запрос"), ("rew", "переписанный запрос")):
        llm_rows = sweep_table(rtx, corpus, off, qt, "llm", LLM_SWEEP)
        cos_rows = sweep_table(rtx, corpus, off, qt, "cos", COS_SWEEP)
        print_sweep(f"Порог LLM-скора релевантности (0–10) · {qt_label}", llm_rows,
                    len(corpus), len(off))
        print_sweep("Порог косинусной близости · " + qt_label, cos_rows, len(corpus), len(off))
        result[qt] = {"llm": llm_rows, "cos": cos_rows,
                      "llm_choice": choose_threshold(llm_rows),
                      "cos_choice": choose_threshold(cos_rows)}
    return result


# --------------------------------------------------------------------------- #
# Датасет: 10 вопросов day-2 + 5 сложных + 3 вне-корпусных
# --------------------------------------------------------------------------- #


def load_dataset() -> list[dict]:
    if not DAY2_QUESTIONS.exists():
        sys.exit(f"[ОШИБКА] Файл вопросов day-2 не найден: {DAY2_QUESTIONS}")
    base = json.loads(DAY2_QUESTIONS.read_text(encoding="utf-8"))["questions"]
    extra = json.loads(EXTRA_QUESTIONS.read_text(encoding="utf-8"))
    cases = [{**c, "kind": "corpus"} for c in base]
    cases += [{**c, "kind": "hard"} for c in extra["hard"]]
    cases += [{**c, "kind": "off_corpus"} for c in extra["off_corpus"]]
    return cases


# --------------------------------------------------------------------------- #
# Вывод
# --------------------------------------------------------------------------- #


def short(text: str, limit: int = 110) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def print_case_block(case: dict, per_mode: dict[str, dict]) -> None:
    print(f"\n  {case['id']} ({case['kind']}): «{short(case['question'])}»")
    if case["kind"] != "off_corpus":
        print(f"      эталон: {case['source']['doc']} › {' / '.join(case['source']['sections'])}")
    for mode in MODES:
        r = per_mode[mode]
        line = f"      {mode:<9}"
        if mode == "rewrite":
            print(f"      rewrite  → «{short(r['rewritten'], 90)}»")
            line = f"      {'':<9}"
        if case["kind"] == "off_corpus":
            if r["n_kept"] == 0 and r["canned_refusal"]:
                line += f"контекст 0 чанков → отказ на уровне фильтра"
            else:
                verdict = "ОТКАЗ честный" if r["refusal"] else "ОТВЕТ ИЗ ОБЩИХ ЗНАНИЙ"
                line += f"контекст {r['n_kept']} чанков ({r['context_chars']} симв.) · {verdict}"
                if r["fallback"]:
                    line += " [реранк недоступен]"
        else:
            s = r["search"]
            flags = " [hit@1 OK]" if s["hit1"] else " [МИМО top-1]"
            if s["section"]:
                flags += " [секция OK]"
            if r["fallback"]:
                flags += " [реранк недоступен]"
            if MODES[mode]["stage2"]:
                line += f"отбор {r['n_kept']}/{r['n_candidates']}{flags}"
            else:
                line += f"контекст {r['n_kept']} чанков{flags}"
            line += f" · факты {r['facts_ok']}/{len(r['facts'])} · ист: {'да' if r['cites'] else '—'}"
        print(line)
        if case["kind"] == "off_corpus" and not r["canned_refusal"]:
            print(f"        {short(r['answer'], 130)}")


def print_summary(cases: list[dict], results: dict[str, dict[str, dict]], rtx: Runtime) -> dict:
    corpus = [c for c in cases if c["kind"] != "off_corpus"]
    off = [c for c in cases if c["kind"] == "off_corpus"]
    n_facts = sum(len(c["expected_facts"]) for c in corpus)

    summary: dict[str, dict] = {}
    for mode in MODES:
        rs = [results[c["id"]][mode] for c in cases]
        cr = [results[c["id"]][mode] for c in corpus]
        ors = [results[c["id"]][mode] for c in off]
        summary[mode] = {
            "facts": sum(r["facts_ok"] for r in cr),
            "facts_all": sum(r["facts_all"] for r in cr),
            "cites": sum(r["cites"] for r in cr),
            "hit1": sum(r["search"]["hit1"] for r in cr),
            "hitk": sum(r["search"]["hitk"] for r in cr),
            "section": sum(r["search"]["section"] for r in cr),
            "off_empty": sum(1 for r in ors if r["n_kept"] == 0),
            "off_refusal": sum(r["refusal"] for r in ors),
            "ctx_corpus": sum(r["context_chars"] for r in cr) // max(len(cr), 1),
            "ctx_off": sum(r["context_chars"] for r in ors) // max(len(ors), 1),
        }

    print("\n" + "=" * 78)
    print("[4] Итоговое сравнение режимов")
    print("=" * 78)
    print(f"    корпус: {len(corpus)} вопросов ({n_facts} фактов) · вне базы: {len(off)} вопросов\n")
    header = f"  {'метрика':<34}" + "".join(f"{m:>11}" for m in MODES)
    print(header)
    n, no = len(corpus), len(off)
    rows = [
        ("фактов подтверждено", lambda s: f"{s['facts']}/{n_facts}"),
        ("вопросов со всеми фактами", lambda s: f"{s['facts_all']}/{n}"),
        ("источник-документ назван", lambda s: f"{s['cites']}/{n}"),
        ("поиск: hit@1 / hit@K / секция", lambda s: f"{s['hit1']}/{s['hitk']}/{s['section']}"),
        ("вне базы: пустой контекст", lambda s: f"{s['off_empty']}/{no}"),
        ("вне базы: честный отказ", lambda s: f"{s['off_refusal']}/{no}"),
        ("средний контекст, симв. (корпус)", lambda s: f"~{s['ctx_corpus']}"),
        ("средний контекст, симв. (вне базы)", lambda s: f"~{s['ctx_off']}"),
    ]
    for label, get in rows:
        print(f"  {label:<34}" + "".join(f"{get(summary[m]):>11}" for m in MODES))
    return summary


# --------------------------------------------------------------------------- #
# Демо одного вопроса (--question)
# --------------------------------------------------------------------------- #


def demo(question: str, rtx: Runtime) -> None:
    print("\n" + "=" * 78)
    print(f"[2] Вопрос: «{question}»")
    print("=" * 78)

    rewritten = rewrite_query(question, rtx.rewrite_cache)
    print(f"\n  [rewrite] поисковый запрос: «{rewritten}»")

    case = {"id": "DEMO", "question": question, "kind": "off_corpus", "expected_facts": []}
    cands = rtx.candidates(case, "rew")
    scores = rtx.llm_scores(case, "rew")
    print(f"\n  [поиск] кандидаты топ-{len(cands)} (по переписанному запросу):")
    kept, dropped, fallback = apply_stage2(
        rtx.stage2, cands, scores, rtx.k2, rtx.llm_threshold, rtx.cos_threshold
    )
    kept_ids = {id(c) for c, _, _ in kept}
    for (c, cos), s in zip(cands, scores if scores else [None] * len(cands)):
        verdict = "В КОНТЕКСТ" if id(c) in kept_ids else (
            f"отсечён (скор {s} < {rtx.llm_threshold:g})" if s is not None else "не прошёл")
        print(f"    [{cos:.3f}] (скор {s if s is not None else '—'}) "
              f"{c['doc']} · {heading_of(c)} — {verdict}")
    if fallback:
        print("    [реранкер недоступен — отбор по косинусной близости]")

    print(f"\n  [ответ] full (rewrite + реранк + фильтр, топ-{len(kept)}):")
    print(f"    {'(контекст пуст — честный отказ)' if not kept else ''}")
    answer, _ = answer_question(question, kept)
    print("    " + answer.replace("\n", "\n    "))

    base_cands = rtx.candidates(case, "orig")
    print(f"\n  [ответ] baseline (без всего, топ-{len(base_cands)} в лоб):")
    answer_base, _ = answer_question(question, base_cands)
    print("    " + answer_base.replace("\n", "\n    "))


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Week 5 Day 3 — reranking & filtering + query rewrite и сравнение режимов"
    )
    parser.add_argument("--question", help="демо пайплайна на одном вопросе")
    parser.add_argument("--index", default=str(DEFAULT_INDEX), help="путь к индексу day-1")
    parser.add_argument("--k1", type=int, default=K1_DEFAULT, help="top-K ДО отбора (кандидаты)")
    parser.add_argument("--k2", type=int, default=K2_DEFAULT, help="top-K ПОСЛЕ отбора (в контекст)")
    parser.add_argument("--threshold", type=float, help="порог LLM-скора (по умолчанию выбирает sweep)")
    parser.add_argument("--cos-threshold", type=float, help="порог косинуса для --stage2 threshold")
    parser.add_argument("--stage2", choices=("rerank", "threshold"), default="rerank",
                        help="тип второго этапа: LLM-реранкер или чистый косинусный фильтр")
    parser.add_argument("--modes", default=",".join(MODES), help="подмножество режимов через запятую")
    parser.add_argument("--sweep-only", action="store_true", help="только настройка порогов")
    args = parser.parse_args()

    load_dotenv(BASE_DIR / ".env")

    unknown = [m for m in args.modes.split(",") if m not in MODES]
    if unknown:
        sys.exit(f"[ОШИБКА] Неизвестные режимы: {unknown}. Доступны: {', '.join(MODES)}")
    active_modes = [m for m in MODES if m in args.modes.split(",")]

    print("Week 5 · Day 3 — Reranking & Filtering "
          "(второй этап после поиска + query rewrite + сравнение режимов)")

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
    print(f"    второй этап: {args.stage2} · top-K: {args.k1} до отбора → {args.k2} после")

    embedder = QueryEmbedder(emb_meta["model"])
    rtx = Runtime(
        index, embedder, args.k1, args.k2, args.stage2,
        args.threshold if args.threshold is not None else LLM_THRESHOLD_DEFAULT,
        args.cos_threshold if args.cos_threshold is not None else COS_THRESHOLD_DEFAULT,
    )

    if args.question:
        demo(args.question, rtx)
        print("\nГотово.")
        return

    cases = load_dataset()
    n_facts = sum(len(c["expected_facts"]) for c in cases if c["kind"] != "off_corpus")
    print(f"    вопросы: {sum(1 for c in cases if c['kind'] == 'corpus')} day-2 + "
          f"{sum(1 for c in cases if c['kind'] == 'hard')} сложных + "
          f"{sum(1 for c in cases if c['kind'] == 'off_corpus')} вне-корпусных "
          f"({n_facts} фактов)")

    # [2] настройка порогов
    print("\n" + "=" * 78)
    print("[2] Настройка порога отсечения (sweep без генерации ответов)")
    print("=" * 78)
    sweep = run_sweep(rtx, cases)
    if args.threshold is None:
        rtx.llm_threshold = sweep["rew"]["llm_choice"]
    if args.cos_threshold is None:
        rtx.cos_threshold = sweep["rew"]["cos_choice"]
    print(f"\n    Выбран порог: LLM-скор ≥ {rtx.llm_threshold:g}"
          f" · косинус ≥ {rtx.cos_threshold:.2f} (по переписанному запросу)")
    if args.sweep_only:
        print("\nГотово (--sweep-only: ответы не генерировались).")
        return

    # [3] прогон всех вопросов во всех режимах
    print("\n" + "=" * 78)
    print(f"[3] Контрольные вопросы × режимы "
          f"(top-K: {rtx.k1} → {rtx.k2}, порог LLM-скора {rtx.llm_threshold:g})")
    print("=" * 78)
    results: dict[str, dict[str, dict]] = {}
    for case in cases:
        per_mode = {mode: run_mode(case, mode, rtx) for mode in active_modes}
        results[case["id"]] = per_mode
        print_case_block(case, per_mode)

    summary = print_summary(cases, results, rtx)

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
                    "stage2": rtx.stage2,
                    "llm_threshold": rtx.llm_threshold,
                    "cos_threshold": rtx.cos_threshold,
                    "modes": active_modes,
                },
                "sweep": sweep,
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
