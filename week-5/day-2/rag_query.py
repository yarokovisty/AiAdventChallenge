"""
Week 5 · Day 2 — First RAG Query + Comparison.

Задание: первая полноценная RAG-цепочка «вопрос → поиск релевантных чанков →
контекст и вопрос → LLM» и сравнение ответов с RAG и без него. Контрольные
вопросы (10 штук) снабжены ожидаемыми фактами и эталонными источниками.

Пайплайн RAG-режима:
  вопрос → эмбеддинг (та же модель, что строила индекс day-1, префикс «query:»)
         → косинусная близость с чанками index/structured.json → топ-K чанков
         → промпт «system + контекст + вопрос»
         → Z.ai GLM (glm-4.6, temperature=0) → ответ со ссылкой на источник.

Рядом — режим «без RAG»: тот же вопрос, та же модель, тот же temperature,
но контекста нет. Сравнение честное: единственное отличие режимов — найденные
чанки в промпте.

Оценка (eval_questions.json, 10 вопросов):
  • факты  — доля ожидаемых фактов, подтверждённых в ответе (вхождение
             допустимых формулировок, регистронезависимо);
  • источник — назван ли ожидаемый документ в ответе (цитирование);
  • поиск  — hit@1 / hit@K по документу и попадание top-1 в эталонную секцию
             (качество именно поискового звена, до LLM).

Запуск:
    python rag_query.py                     # полный прогон: 10 вопросов × 2 режима
    python rag_query.py --question "..."    # один вопрос: оба ответа + найденные чанки
    python rag_query.py --k 6               # другой топ-K (по умолчанию 4)

Индекс берётся из day-1 (по умолчанию ../day-1/index/structured.json); если
его нет — сначала запустите build_index.py из day-1. Ключ ZAI_API_KEY хранится
в .env (gitignored), в git не попадает.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import requests
from dotenv import load_dotenv

# Тихий режим кэша HuggingFace на Windows (как в day-1).
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_INDEX = BASE_DIR.parent / "day-1" / "index" / "structured.json"
QUESTIONS_PATH = BASE_DIR / "eval_questions.json"
RESULTS_DIR = BASE_DIR / "results"

# OpenAI-совместимый coding-эндпоинт Z.ai (тот же, что в неделе 4).
ZAI_CHAT_URL_DEFAULT = "https://api.z.ai/api/coding/paas/v4/chat/completions"
ZAI_MODEL_DEFAULT = "glm-4.6"
TEMPERATURE = 0.0          # детерминированность: режимы отличаются только контекстом
TOP_K_DEFAULT = 4
REQUEST_TIMEOUT = 120

# System-промпты. Отличие режимов — ТОЛЬКО доступ к контексту и цитирование.
SYSTEM_BASE = (
    "Ты — аккуратный технический ассистент. Отвечай по-русски, кратко и по делу "
    "(2–6 предложений): только суть вопроса, без воды."
)
SYSTEM_RAG = (
    "Ты — ассистент с доступом к базе знаний курса. Отвечай по-русски, кратко "
    "(2–6 предложений). Пользуйся ТОЛЬКО приведённым контекстом: если ответа в "
    "нём нет — честно скажи об этом. В конце ответа обязательно укажи источник "
    "в формате [имя_файла › раздел]."
)


# --------------------------------------------------------------------------- #
# Ошибки LLM-вызова: retry только на сетевых/серверных сбоях.
# --------------------------------------------------------------------------- #


class LlmRetryableError(RuntimeError):
    """Сетевой сбой или 429/5xx — имеет смысл повторить."""


class LlmFatalError(RuntimeError):
    """Ошибка без смысла повторять (нет ключа, 401, кривой запрос)."""


# --------------------------------------------------------------------------- #
# Индекс и поиск
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
    """Эмбеддер запросов — та же модель, что индексировала чанки (её имя —
    в метаданных индекса). E5 требует префикс «query: » для поисковых запросов."""

    def __init__(self, model_name: str):
        from sentence_transformers import SentenceTransformer

        print(f"    Загрузка модели запросов {model_name} ...")
        self.model = SentenceTransformer(model_name)

    def embed_query(self, text: str) -> list[float]:
        vec = self.model.encode(
            [f"query: {text}"], normalize_embeddings=True, show_progress_bar=False
        )
        return vec[0].astype(float).tolist()


def retrieve(chunks: list[dict], query_vec: list[float], k: int) -> list[tuple[dict, float]]:
    """Топ-K чанков по косинусной близости (векторы нормированы)."""
    matrix = np.array([c["vector"] for c in chunks])
    sims = matrix @ np.array(query_vec)
    order = np.argsort(-sims)[:k]
    return [(chunks[i], float(sims[i])) for i in order]


def heading_of(chunk: dict) -> str:
    path = chunk.get("heading_path") or []
    return " › ".join(path[1:]) if len(path) > 1 else (" › ".join(path) if path else "—")


def build_context(hits: list[tuple[dict, float]]) -> str:
    """Найденные чанки → нумерованный контекст для промпта. У structured-чанков
    текст уже начинается с «паспорта» — пути заголовков секции."""
    parts = []
    for i, (c, _) in enumerate(hits, start=1):
        parts.append(f"[{i}] ({c['doc']})\n{c['text']}")
    return "\n\n".join(parts)


# --------------------------------------------------------------------------- #
# LLM (Z.ai GLM, OpenAI-совместимый протокол — как в неделе 4)
# --------------------------------------------------------------------------- #


def chat_completion(question: str, context: str | None) -> str:
    api_key = os.getenv("ZAI_API_KEY")
    if not api_key or api_key == "your_api_key_here":
        raise LlmFatalError(
            "ZAI_API_KEY не задан: скопируйте .env.example в .env и впишите ключ."
        )
    url = os.getenv("ZAI_CHAT_URL", ZAI_CHAT_URL_DEFAULT)
    model = os.getenv("ZAI_MODEL", ZAI_MODEL_DEFAULT)

    messages: list[dict] = [
        {"role": "system", "content": SYSTEM_RAG if context else SYSTEM_BASE}
    ]
    if context:
        messages.append(
            {
                "role": "user",
                "content": f"Контекст из базы знаний:\n\n{context}\n\nВопрос: {question}",
            }
        )
    else:
        messages.append({"role": "user", "content": question})

    body = {
        "model": model,
        "messages": messages,
        "temperature": TEMPERATURE,
        "thinking": {"type": "disabled"},  # короткие фактические ответы, без размышлений
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
# Авто-оценка ответов
# --------------------------------------------------------------------------- #


def check_facts(answer: str, expected_facts: list[dict]) -> list[dict]:
    """Каждый факт задан списком допустимых формулировок; проверяем вхождение
    (регистронезависимо) хотя бы одной в ответ."""
    low = answer.lower()
    return [
        {
            "fact": f["fact"],
            "ok": any(v.lower() in low for v in f["any_of"]),
        }
        for f in expected_facts
    ]


def cites_source(answer: str, doc: str) -> bool:
    """Цитирование: ответ называет ожидаемый документ (с .md или без)."""
    low = answer.lower()
    stem = doc[:-3] if doc.endswith(".md") else doc
    return doc.lower() in low or stem.lower() in low


def search_hit(hits: list[tuple[dict, float]], source: dict) -> dict:
    """Качество поиска до LLM: top-1 по документу, документ в топ-K, секция."""
    docs = [c["doc"] for c, _ in hits]
    top1_path = hits[0][0].get("heading_path") or []
    return {
        "hit1": docs[0] == source["doc"],
        "hitk": source["doc"] in docs,
        "section": bool(set(source["sections"]) & set(top1_path)),
        "top1_doc": docs[0],
        "top1_heading": heading_of(hits[0][0]),
        "top1_score": round(hits[0][1], 3),
    }


# --------------------------------------------------------------------------- #
# Режимы запуска
# --------------------------------------------------------------------------- #


def demo_single_question(question: str, index: dict, embedder, k: int) -> None:
    print("\n" + "=" * 78)
    print(f"[2] Вопрос: «{question}»")
    print("=" * 78)

    hits = retrieve(index["chunks"], embedder.embed_query(question), k)
    print(f"\n  Поиск: топ-{k} чанков индекса «{index['strategy']}»")
    for rank, (c, s) in enumerate(hits, start=1):
        print(f"   {rank}. [{s:.3f}] {c['doc']} · {heading_of(c)}")
        snippet = " ".join(c["text"].split())[:140]
        print(f"      {snippet}...")

    print("\n  ── Ответ БЕЗ RAG (тот же вопрос, без контекста) " + "─" * 20)
    print(indent(chat_completion(question, context=None), "    "))

    print("\n  ── Ответ С RAG (контекст + вопрос) " + "─" * 44)
    print(indent(chat_completion(question, context=build_context(hits)), "    "))


def indent(text: str, pad: str) -> str:
    return "\n".join(pad + line for line in text.splitlines())


def short(text: str, limit: int = 200) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def run_evaluation(index: dict, embedder, k: int) -> None:
    cases = json.loads(QUESTIONS_PATH.read_text(encoding="utf-8"))["questions"]
    n_facts_total = sum(len(c["expected_facts"]) for c in cases)

    print("\n" + "=" * 78)
    print(f"[2] Контрольные вопросы: поиск топ-{k} → два ответа → авточек")
    print("=" * 78)

    rows: list[dict] = []
    for idx, case in enumerate(cases, start=1):
        q = case["question"]
        src = case["source"]
        print(f"\n  {case['id']}: «{short(q, 110)}»")
        print(f"      эталон: {src['doc']} › {' / '.join(src['sections'])}")

        hits = retrieve(index["chunks"], embedder.embed_query(q), k)
        sh = search_hit(hits, src)
        flags = ""
        flags += " [документ OK]" if sh["hit1"] else " [МИМО документа]"
        flags += " [секция OK]" if sh["section"] else ""
        print(f"      поиск:  top-1 {sh['top1_doc']} · {short(sh['top1_heading'], 44)} "
              f"{sh['top1_score']}{flags}")

        answer_plain = chat_completion(q, context=None)
        facts_plain = check_facts(answer_plain, case["expected_facts"])
        cite_plain = cites_source(answer_plain, src["doc"])
        ok_plain = sum(f["ok"] for f in facts_plain)
        print(f"      без RAG:  факты {ok_plain}/{len(facts_plain)} · "
              f"источник: {'назван' if cite_plain else '—'}")
        print(f"        {short(answer_plain)}")

        answer_rag = chat_completion(q, context=build_context(hits))
        facts_rag = check_facts(answer_rag, case["expected_facts"])
        cite_rag = cites_source(answer_rag, src["doc"])
        ok_rag = sum(f["ok"] for f in facts_rag)
        print(f"      с RAG:    факты {ok_rag}/{len(facts_rag)} · "
              f"источник: {'назван' if cite_rag else '—'}")
        print(f"        {short(answer_rag)}")
        for f in facts_rag:
            if not f["ok"]:
                print(f"        [пропущен факт] {f['fact']}")

        rows.append(
            {
                "id": case["id"],
                "question": q,
                "source": src,
                "retrieved": [
                    {"doc": c["doc"], "heading": heading_of(c), "score": round(s, 3)}
                    for c, s in hits
                ],
                "search": sh,
                "expected_facts": case["expected_facts"],
                "no_rag": {"answer": answer_plain, "facts": facts_plain, "cites": cite_plain},
                "rag": {"answer": answer_rag, "facts": facts_rag, "cites": cite_rag},
            }
        )

    # ------------------------------------------------------------------ #
    print("\n" + "=" * 78)
    print("[3] Итоговое сравнение")
    print("=" * 78)
    n = len(rows)
    facts_plain = sum(sum(f["ok"] for f in r["no_rag"]["facts"]) for r in rows)
    facts_rag = sum(sum(f["ok"] for f in r["rag"]["facts"]) for r in rows)
    full_plain = sum(all(f["ok"] for f in r["no_rag"]["facts"]) for r in rows)
    full_rag = sum(all(f["ok"] for f in r["rag"]["facts"]) for r in rows)
    cite_plain = sum(r["no_rag"]["cites"] for r in rows)
    cite_rag = sum(r["rag"]["cites"] for r in rows)
    hit1 = sum(r["search"]["hit1"] for r in rows)
    hitk = sum(r["search"]["hitk"] for r in rows)
    section = sum(r["search"]["section"] for r in rows)

    table = [
        ("фактов подтверждено", f"{facts_plain}/{n_facts_total}", f"{facts_rag}/{n_facts_total}"),
        ("вопросов со всеми фактами", f"{full_plain}/{n}", f"{full_rag}/{n}"),
        ("источник-документ назван", f"{cite_plain}/{n}", f"{cite_rag}/{n}"),
        ("поиск: hit@1 / hit@K / секция", "—", f"{hit1}/{n} · {hitk}/{n} · {section}/{n}"),
    ]
    print(f"  {'метрика':<32}{'без RAG':<14}{'с RAG'}")
    for label, a, b in table:
        print(f"  {label:<32}{a:<14}{b}")

    RESULTS_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = RESULTS_DIR / f"eval_{stamp}.json"
    out.write_text(
        json.dumps(
            {
                "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "index": {"strategy": index["strategy"], "embedding": index["embedding"]},
                "model": os.getenv("ZAI_MODEL", ZAI_MODEL_DEFAULT),
                "temperature": TEMPERATURE,
                "top_k": k,
                "summary": {
                    "facts_total": n_facts_total,
                    "facts_no_rag": facts_plain,
                    "facts_rag": facts_rag,
                    "all_facts_no_rag": full_plain,
                    "all_facts_rag": full_rag,
                    "cites_no_rag": cite_plain,
                    "cites_rag": cite_rag,
                    "search_hit1": hit1,
                    "search_hitk": hitk,
                    "search_section": section,
                },
                "results": rows,
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    print(f"\n  Результаты сохранены: {out}")


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Week 5 Day 2 — первый RAG-запрос и сравнение с/без RAG"
    )
    parser.add_argument("--question", help="один вопрос: показать оба ответа и чанки")
    parser.add_argument("--index", default=str(DEFAULT_INDEX), help="путь к индексу day-1")
    parser.add_argument("--k", type=int, default=TOP_K_DEFAULT, help="размер топ-K (по умолчанию 4)")
    args = parser.parse_args()

    load_dotenv(BASE_DIR / ".env")

    print("Week 5 · Day 2 — First RAG Query + Comparison (цепочка RAG и сравнение с/без RAG)")

    print("\n" + "=" * 78)
    print("[1] Индекс, эмбеддинги и LLM")
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

    embedder = QueryEmbedder(emb_meta["model"])

    if args.question:
        demo_single_question(args.question, index, embedder, args.k)
    else:
        run_evaluation(index, embedder, args.k)

    print("\nГотово.")


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    main()
