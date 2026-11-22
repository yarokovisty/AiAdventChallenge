"""
Week 5 · Day 1 — RAG: Document Indexing.

Задание: собрать локальный индекс документов с эмбеддингами и метаданными;
сравнить разбиение по фиксированному размеру и по структуре.

Пайплайн скрипта:
  documents/*.md → чанкинг (2 стратегии) → эмбеддинги → локальный индекс
  index/fixed.json + index/structured.json → smoke-проверка поиском (косинусная
  близость, топ-3) на тестовых запросах с эталонными секциями → отчёт-сравнение.

Стратегии чанкинга:
  • fixed      — по фиксированному размеру: ~CHUNK_SIZE символов с перекрытием
                 CHUNK_OVERLAP, разрез «в лоб» по словам, без оглядки на
                 предложения и заголовки (классический baseline);
  • structured — по структуре документа: секции по заголовкам Markdown, мелкие
                 абзацы склеиваются до целевого размера, гигантские режутся по
                 границам предложений; путь заголовков пишется и в текст чанка,
                 и в метаданные.

Эмбеддинги: провайдер выбирается автоматически.
  1) Z.ai embeddings API (api.z.ai) — если ключ ZAI_API_KEY задан и endpoint
     доступен. На глобальном endpoint Z.ai embeddings-моделей нет (проверено
     живым вызовом: /models отдаёт только чат-модели, /embeddings → 1211
     Unknown Model), поэтому попытка честно фейлится с выводом причины.
  2) Локальная модель sentence-transformers (по умолчанию
     intfloat/multilingual-e5-small) — полностью офлайн после первой загрузки.

Запуск:
    python build_index.py                    # сборка индексов + отчёт + тесты
    python build_index.py --query "..."      # то же + поиск по обоим индексам

Ключ хранится в .env (gitignored), в git не попадает.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

# Тихий режим кэша HuggingFace на Windows (без этого предупреждение про symlinks
# при каждой загрузке модели).
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

# ---------------------------------------------------------------------------
# Параметры (подобраны под корпус из 5 markdown-документов)
# ---------------------------------------------------------------------------
CHUNK_SIZE = 600        # fixed: целевой размер чанка в символах
CHUNK_OVERLAP = 100     # fixed: перекрытие соседних чанков в символах

STRUCT_TARGET = 600     # structured: целевой размер блока контента секции
STRUCT_HARD_MAX = 900   # structured: жёсткий потолок, дальше режем по предложениям

ZAI_EMBED_URL_DEFAULT = "https://api.z.ai/api/paas/v4/embeddings"
ZAI_EMBED_MODEL_DEFAULT = "embedding-3"
LOCAL_EMBED_MODEL_DEFAULT = "intfloat/multilingual-e5-small"

EVAL_QUERIES = [
    {
        "q": "Куда выставлять temperature для творческих задач и что она вообще меняет?",
        "doc": "01-prompting-basics.md",
        "sections": ["Temperature"],
    },
    {
        "q": "Что делать, если вызов инструмента у агента упал с ошибкой?",
        "doc": "02-agents-and-tools.md",
        "sections": ["Обработка ошибок инструмента"],
    },
    {
        "q": "Где хранить постоянные сведения о пользователе между сессиями?",
        "doc": "03-context-management.md",
        "sections": ["Профиль пользователя и персонализация"],
    },
    {
        "q": "В чём разница между транспортом stdio и streamable HTTP в MCP?",
        "doc": "04-mcp.md",
        "sections": ["Транспорт stdio", "Транспорт Streamable HTTP"],
    },
    {
        "q": "Зачем резать документы по заголовкам, а не по количеству символов?",
        "doc": "05-rag.md",
        "sections": ["Фиксированный размер", "Структурное разбиение"],
    },
    {
        "q": "Какими метриками меряют качество поиска в RAG?",
        "doc": "05-rag.md",
        "sections": ["Оценка качества RAG"],
    },
]

TERMINAL_CHARS = tuple(".!?…:;»)\"]")

BASE_DIR = Path(__file__).resolve().parent
DOCS_DIR = BASE_DIR / "documents"
INDEX_DIR = BASE_DIR / "index"

# ---------------------------------------------------------------------------
# Загрузка корпуса
# ---------------------------------------------------------------------------


def load_corpus() -> list[dict]:
    docs = []
    for path in sorted(DOCS_DIR.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        docs.append(
            {
                "name": path.name,
                "text": text,
                "sha1": hashlib.sha1(text.encode("utf-8")).hexdigest()[:12],
                "chars": len(text),
            }
        )
    if not docs:
        sys.exit(f"[ОШИБКА] В {DOCS_DIR} нет .md файлов.")
    return docs


# ---------------------------------------------------------------------------
# Стратегия 1: fixed — фиксированный размер с перекрытием
# ---------------------------------------------------------------------------


def chunk_fixed(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Режет текст на куски ~size символов с перекрытием ~overlap.

    Классический baseline: границы слов не ломаем (иначе эмбеддинг получает
    обрывки слов), но на предложения и структуру не смотрим вовсе.
    """
    flat = re.sub(r"\s+", " ", text).strip()
    words = flat.split(" ")

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for word in words:
        add = len(word) + (1 if current else 0)
        if current and current_len + add > size:
            chunks.append(" ".join(current))
            # хвост предыдущего чанка ~overlap символов — в начало следующего
            tail: list[str] = []
            tail_len = 0
            for w in reversed(current):
                if tail_len + len(w) + 1 > overlap:
                    break
                tail.insert(0, w)
                tail_len += len(w) + 1
            current = tail
            current_len = tail_len
        current.append(word)
        current_len += len(word) + (1 if len(current) > 1 else 0)
    if current:
        chunks.append(" ".join(current))
    return chunks


# ---------------------------------------------------------------------------
# Стратегия 2: structured — по структуре документа (заголовки/абзацы)
# ---------------------------------------------------------------------------

HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")


def parse_sections(md_text: str) -> list[dict]:
    """Раскладывает markdown на секции: путь заголовков + строки контента.

    Блоки кода (``` ... ```) не разрезаются считаются за один абзац.
    """
    sections: list[dict] = []
    stack: list[tuple[int, str]] = []  # (уровень, заголовок)
    current: dict | None = None
    in_fence = False

    def close() -> None:
        nonlocal current
        if current is not None and any(ln.strip() for ln in current["lines"]):
            sections.append(current)
        current = None

    for line in md_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            if current is None:
                current = {"level": 0, "title": "", "path": [], "lines": []}
            current["lines"].append(line)
            continue
        if not in_fence and (m := HEADING_RE.match(line)):
            close()
            level, title = len(m.group(1)), m.group(2).strip()
            stack = [(lvl, ttl) for lvl, ttl in stack if lvl < level]
            stack.append((level, title))
            current = {
                "level": level,
                "title": title,
                "path": [ttl for _, ttl in stack],
                "lines": [],
            }
        else:
            if current is None:
                current = {"level": 0, "title": "", "path": [], "lines": []}
            current["lines"].append(line)
    if not in_fence:
        close()
    elif current is not None:
        sections.append(current)
    return sections


def split_paragraphs(lines: list[str]) -> list[str]:
    """Абзацы = блоки между пустыми строками; блоки кода атомарны."""
    paragraphs: list[str] = []
    buf: list[str] = []
    fence = False
    for line in lines:
        if line.strip().startswith("```"):
            fence = not fence
            buf.append(line)
            if not fence:  # конец блока кода — закрыть абзац
                paragraphs.append("\n".join(buf).strip())
                buf = []
            continue
        if not fence and not line.strip():
            if buf:
                paragraphs.append("\n".join(buf).strip())
                buf = []
        else:
            buf.append(line)
    if buf:
        paragraphs.append("\n".join(buf).strip())
    return [p for p in paragraphs if p]


def split_sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?…])\s+", text) if s]


def pack_blocks(blocks: list[str], target: int, hard_max: int) -> list[str]:
    """Жадно упаковывает абзацы в блоки ~target; гигантские режет по предложениям."""
    packed: list[str] = []
    current = ""
    for block in blocks:
        if len(block) > hard_max:
            if current:
                packed.append(current)
                current = ""
            sentences = split_sentences(block)
            sent_buf = ""
            for sent in sentences:
                if len(sent) > hard_max:  # совсем монструозное предложение
                    if sent_buf:
                        packed.append(sent_buf)
                        sent_buf = ""
                    packed.append(sent)
                    continue
                if sent_buf and len(sent_buf) + 1 + len(sent) > hard_max:
                    packed.append(sent_buf)
                    sent_buf = sent
                else:
                    sent_buf = f"{sent_buf} {sent}".strip()
            if sent_buf:
                packed.append(sent_buf)
        elif current and len(current) + 2 + len(block) > target:
            packed.append(current)
            current = block
        else:
            current = f"{current}\n\n{block}".strip() if current else block
    if current:
        packed.append(current)
    return packed


def chunk_structured(md_text: str) -> list[dict]:
    """Секции по заголовкам → упаковка абзацев; путь заголовков — в метаданные."""
    chunks: list[dict] = []
    for section in parse_sections(md_text):
        paragraphs = split_paragraphs(section["lines"])
        if not paragraphs:
            continue
        # строка-«паспорт» секции кладётся в текст чанка: эмбеддинг знает раздел
        breadcrumb = " > ".join(section["path"])
        for block in pack_blocks(paragraphs, STRUCT_TARGET, STRUCT_HARD_MAX):
            chunks.append(
                {
                    "text": f"{breadcrumb}\n\n{block}" if breadcrumb else block,
                    "heading_path": section["path"],
                }
            )
    return chunks


# ---------------------------------------------------------------------------
# Эмбеддинг-провайдеры
# ---------------------------------------------------------------------------


def _normalize(vectors: list[list[float]]) -> list[list[float]]:
    arr = np.asarray(vectors, dtype=np.float64)
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (arr / norms).tolist()


class ZaiEmbeddingProvider:
    """Embeddings API Z.ai (OpenAI-совместимый формат ответа)."""

    name = "zai-api"

    def __init__(self, api_key: str, model: str, url: str):
        import requests

        self._requests = requests
        self.api_key = api_key
        self.model = model
        self.url = url
        # проверка доступности одним дешёвым вызовом
        self.embed_documents(["проверка соединения"])

    def _call(self, texts: list[str]) -> list[list[float]]:
        resp = self._requests.post(
            self.url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={"model": self.model, "input": texts},
            timeout=90,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:160]}")
        data = sorted(resp.json()["data"], key=lambda d: d["index"])
        return [d["embedding"] for d in data]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), 64):  # лимит embedding-3: 64 текста/запрос
            out.extend(self._call(texts[i : i + 64]))
        return _normalize(out)

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]


class LocalEmbeddingProvider:
    """Локальная sentence-transformers модель (intfloat/multilingual-e5-small).

    E5 требует префиксы: «query: » для поисковых запросов и «passage: » для
    индексируемых документов — без них качество заметно проседает.
    """

    name = "local-e5"

    def __init__(self, model_name: str):
        from sentence_transformers import SentenceTransformer

        print(f"  Загрузка локальной модели {model_name} "
              "(первый запуск скачивает её в кэш HuggingFace)...")
        self.model = SentenceTransformer(model_name)
        self.model_name = model_name

    def _encode(self, texts: list[str], kind: str) -> list[list[float]]:
        prefixed = [f"{kind}: {t}" for t in texts]
        vectors = self.model.encode(
            prefixed, batch_size=32, normalize_embeddings=True, show_progress_bar=False,
        )
        return vectors.astype(float).tolist()

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._encode(texts, "passage")

    def embed_query(self, text: str) -> list[float]:
        return self._encode([text], "query")[0]


def pick_provider() -> ZaiEmbeddingProvider | LocalEmbeddingProvider:
    """Сначала пробуем Z.ai (если ключ задан), при отказе — локальная модель."""
    api_key = os.getenv("ZAI_API_KEY")
    if api_key and api_key != "your_api_key_here":
        url = os.getenv("ZAI_EMBED_URL", ZAI_EMBED_URL_DEFAULT)
        model = os.getenv("ZAI_EMBED_MODEL", ZAI_EMBED_MODEL_DEFAULT)
        print(f"  Пробуем Z.ai embeddings API: {url} (модель {model})...")
        try:
            return ZaiEmbeddingProvider(api_key, model, url)
        except Exception as exc:  # noqa: BLE001 — любая причина отказа → фолбэк
            print(f"  [Z.ai недоступен] {exc}")
            print("  Переходим на локальную модель (см. README дня).")
    else:
        print("  ZAI_API_KEY не задан — сразу локальная модель.")
    return LocalEmbeddingProvider(os.getenv("LOCAL_EMBED_MODEL", LOCAL_EMBED_MODEL_DEFAULT))


# ---------------------------------------------------------------------------
# Индекс
# ---------------------------------------------------------------------------


def build_chunks(docs: list[dict]) -> tuple[list[dict], list[dict]]:
    fixed_chunks: list[dict] = []
    structured_chunks: list[dict] = []

    for doc in docs:
        for pos, text in enumerate(chunk_fixed(doc["text"])):
            fixed_chunks.append(
                {
                    "doc": doc["name"],
                    "pos": pos,
                    "chars": len(text),
                    "text": text,
                }
            )
        for pos, chunk in enumerate(chunk_structured(doc["text"])):
            structured_chunks.append(
                {
                    "doc": doc["name"],
                    "pos": pos,
                    "chars": len(chunk["text"]),
                    "text": chunk["text"],
                    "heading_path": chunk["heading_path"],
                }
            )

    for i, c in enumerate(fixed_chunks):
        c["id"] = f"fixed-{i:04d}"
    for i, c in enumerate(structured_chunks):
        c["id"] = f"structured-{i:04d}"
    return fixed_chunks, structured_chunks


def ends_mid_sentence(text: str) -> bool:
    return not text.rstrip().endswith(TERMINAL_CHARS)


def index_stats(chunks: list[dict], docs: list[dict]) -> dict:
    sizes = [c["chars"] for c in chunks]
    # «разрыв мысли»: чанк кончается посреди предложения (кроме последнего в документе)
    last_by_doc = {c["doc"]: c["pos"] for c in chunks}
    broken = sum(
        1
        for c in chunks
        if c["pos"] != last_by_doc[c["doc"]] and ends_mid_sentence(c["text"])
    )
    with_heading_meta = sum(1 for c in chunks if c.get("heading_path"))
    per_doc = {d["name"]: sum(1 for c in chunks if c["doc"] == d["name"]) for d in docs}
    return {
        "chunks": len(chunks),
        "chars_min": min(sizes),
        "chars_avg": round(sum(sizes) / len(sizes)),
        "chars_max": max(sizes),
        "chunks_broken_mid_sentence": broken,
        "chunks_with_heading_metadata": with_heading_meta,
        "chunks_per_doc": per_doc,
    }


def save_index(path: Path, strategy: str, params: dict, chunks: list[dict],
               docs: list[dict], provider, dim: int) -> None:
    payload = {
        "strategy": strategy,
        "params": params,
        "embedding": {
            "provider": provider.name,
            "model": getattr(provider, "model_name", None)
            or os.getenv("ZAI_EMBED_MODEL", ZAI_EMBED_MODEL_DEFAULT),
            "dim": dim,
        },
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "documents": [
            {"name": d["name"], "sha1": d["sha1"], "chars": d["chars"]} for d in docs
        ],
        "stats": index_stats(chunks, docs),
        "chunks": [
            {
                "id": c["id"],
                "doc": c["doc"],
                "pos": c["pos"],
                "chars": c["chars"],
                "heading_path": c.get("heading_path"),
                "text": c["text"],
                # 6 знаков достаточно: косинус меняется в 5-м знаке и дальше
                "vector": [round(v, 6) for v in c["vector"]],
            }
            for c in chunks
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")


# ---------------------------------------------------------------------------
# Поиск и сравнение стратегий
# ---------------------------------------------------------------------------


def top_k(chunks: list[dict], query_vec: list[float], k: int = 3) -> list[tuple[dict, float]]:
    matrix = np.array([c["vector"] for c in chunks])  # векторы нормированы
    sims = matrix @ np.array(query_vec)
    order = np.argsort(-sims)[:k]
    return [(chunks[i], float(sims[i])) for i in order]


def heading_of(chunk: dict) -> str:
    path = chunk.get("heading_path")
    if not path:
        return "—"
    return " › ".join(path[1:]) if len(path) > 1 else path[0]


def run_evaluation(indexes: dict[str, list[dict]], provider) -> None:
    print("\n" + "=" * 78)
    print("[4] Проверка поиска: тестовые запросы с эталонными секциями (top-3)")
    print("=" * 78)

    summary = {name: {"hit1": 0, "hit3": 0, "section_hit": 0} for name in indexes}

    for idx, case in enumerate(EVAL_QUERIES, start=1):
        print(f"\n  Q{idx}: «{case['q']}»")
        print(f"      эталон: {case['doc']} › {' / '.join(case['sections'])}")
        qvec = provider.embed_query(case["q"])
        for name, chunks in indexes.items():
            hits = top_k(chunks, qvec, k=3)
            top1, score1 = hits[0]
            doc_ok_1 = top1["doc"] == case["doc"]
            doc_ok_3 = any(c["doc"] == case["doc"] for c, _ in hits)
            section_ok = bool(
                top1.get("heading_path")
                and set(case["sections"]) & set(top1["heading_path"])
            )
            summary[name]["hit1"] += doc_ok_1
            summary[name]["hit3"] += doc_ok_3
            summary[name]["section_hit"] += section_ok
            print(f"    {name:<11} top-1: {top1['doc'][:28]:<30} {heading_of(top1)[:34]:<36} {score1:.3f}"
                  f"   [{'документ OK' if doc_ok_1 else 'МИМО документа'}]"
                  f"{' [секция OK]' if section_ok else ''}")
            for c, s in hits[1:]:
                print(f"                {c['doc'][:28]:<30} {heading_of(c)[:34]:<36} {s:.3f}")

    print("\n" + "=" * 78)
    print("[5] Итоговое сравнение стратегий")
    print("=" * 78)
    n = len(EVAL_QUERIES)
    header = f"  {'метрика':<38}"
    for name in indexes:
        header += f"{name:>12}"
    print(header)
    rows = [
        ("hit@1 — нужный документ на 1-м месте", "hit1"),
        ("hit@3 — документ в топ-3", "hit3"),
        ("top-1 попал в эталонную секцию", "section_hit"),
    ]
    for label, key in rows:
        line = f"  {label:<38}"
        for name in indexes:
            line += f"{summary[name][key]}/{n}".rjust(12)
        print(line)


def search_cli(indexes: dict[str, list[dict]], provider, query: str) -> None:
    print("\n" + "=" * 78)
    print(f"[6] Поиск по запросу: «{query}»")
    print("=" * 78)
    qvec = provider.embed_query(query)
    for name, chunks in indexes.items():
        print(f"\n  Индекс «{name}», топ-3 по косинусной близости:")
        for rank, (c, s) in enumerate(top_k(chunks, qvec, k=3), start=1):
            print(f"   {rank}. [{s:.3f}] {c['id']} · {c['doc']} · {heading_of(c)}")
            snippet = re.sub(r"\s+", " ", c["text"])[:160]
            print(f"      {snippet}...")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Week 5 Day 1 — локальный индекс документов")
    parser.add_argument("--query", help="дополнительно найти топ-3 по обоим индексам")
    args = parser.parse_args()

    from dotenv import load_dotenv

    load_dotenv(BASE_DIR / ".env")

    print("Week 5 · Day 1 — RAG: Document Indexing (локальный индекс + сравнение чанкинга)")

    # [1] корпус
    print("\n" + "=" * 78)
    print("[1] Корпус документов")
    print("=" * 78)
    docs = load_corpus()
    for d in docs:
        print(f"    {d['name']:<32} {d['chars']:>6} симв.  sha1:{d['sha1']}")
    print(f"    Итого: {len(docs)} документов, {sum(d['chars'] for d in docs)} символов")

    # [2] эмбеддинг-провайдер
    print("\n" + "=" * 78)
    print("[2] Эмбеддинг-провайдер")
    print("=" * 78)
    provider = pick_provider()

    # [3] чанкинг + индексация
    print("\n" + "=" * 78)
    print("[3] Чанкинг и индексация (две стратегии, один провайдер эмбеддингов)")
    print("=" * 78)
    fixed_chunks, structured_chunks = build_chunks(docs)
    for c in fixed_chunks:
        c.pop("vector", None)
    texts_fixed = [c["text"] for c in fixed_chunks]
    texts_struct = [c["text"] for c in structured_chunks]
    t0 = time.time()
    vec_fixed = provider.embed_documents(texts_fixed)
    vec_struct = provider.embed_documents(texts_struct)
    for c, v in zip(fixed_chunks, vec_fixed):
        c["vector"] = v
    for c, v in zip(structured_chunks, vec_struct):
        c["vector"] = v
    print(f"    Эмбеддингов получено: {len(vec_fixed)} (fixed) + {len(vec_struct)} "
          f"(structured) за {time.time() - t0:.1f} с; размерность {len(vec_fixed[0])}")

    params_fixed = {"chunk_size": CHUNK_SIZE, "overlap": CHUNK_OVERLAP, "unit": "символы"}
    params_struct = {
        "target": STRUCT_TARGET,
        "hard_max": STRUCT_HARD_MAX,
        "boundaries": "заголовки markdown → абзацы → предложения",
    }
    for name, chunks, params in (
        ("fixed", fixed_chunks, params_fixed),
        ("structured", structured_chunks, params_struct),
    ):
        st = index_stats(chunks, docs)
        print(f"\n    {name:<11} {st['chunks']} чанков · размер "
              f"мин {st['chars_min']} / средн {st['chars_avg']} / макс {st['chars_max']}")
        print(f"    {'':<11} разорвано посреди предложения: {st['chunks_broken_mid_sentence']} "
              f"({st['chunks_broken_mid_sentence'] * 100 // max(st['chunks'], 1)}%)")
        meta = "doc, позиция, длина"
        if st["chunks_with_heading_metadata"]:
            meta += ", путь заголовков"
        print(f"    {'':<11} метаданные чанка: {meta}")

    INDEX_DIR.mkdir(exist_ok=True)
    dim = len(fixed_chunks[0]["vector"])
    save_index(INDEX_DIR / "fixed.json", "fixed", params_fixed, fixed_chunks, docs, provider, dim)
    save_index(INDEX_DIR / "structured.json", "structured", params_struct, structured_chunks, docs, provider, dim)
    print(f"\n    Индексы сохранены локально: index/fixed.json, index/structured.json")

    # [4]-[5] проверка поиска + итог
    run_evaluation({"fixed": fixed_chunks, "structured": structured_chunks}, provider)

    # [6] опциональный режим поиска
    if args.query:
        search_cli({"fixed": fixed_chunks, "structured": structured_chunks}, provider, args.query)

    print("\nГотово.")


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    main()
