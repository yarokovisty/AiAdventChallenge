"""
Week 5 · Day 5 — RAG-чат с историей и памятью задачи.

Задание: мини-чат, который хранит историю диалога; при каждом новом вопросе
ищет контекст через RAG, отвечает по найденной информации и выводит источники.
Усиление — память задачи: цель, уточнения, ограничения и термины. Проверка —
два длинных сценария по 10–15 сообщений.

Как это устроено (база — пайплайн day-4, всё локальное кроме LLM):

  сообщение пользователя
        │
        ├─ [0] ПАМЯТЬ ЗАДАЧИ (1 LLM-вызов, ДО ответа — чтобы повлиять на него):
        │       из сообщения извлекаются цель / уточнения / ограничения /
        │       запрашиваемые термины → детерминированный merge в память;
        │       • meta_only (правила/цель без вопроса к базе) → короткое
        │         подтверждение «Принято…», RAG не вызывается;
        │       • needs_history (вопрос о самом диалоге: «напомни, о чём мы
        │         говорили») → ответ из истории и памяти, без поиска по базе;
        │       • иначе обычный RAG-ход:
        │
        ├─ [1] query rewrite С ИСТОРИЕЙ И ПАМЯТЬЮ: follow-up с местоимениями
        │       («а если я перееду с ним на VPS?») → самодостаточный
        │       поисковый запрос («MCP-сервер на VPS транспорт Streamable HTTP»)
        ├─ [2] векторный поиск (e5) → топ-K1 = 8 кандидатов
        ├─ [3] LLM-реранкер 0–10 + ПОРОГ (2) → топ-K2 = 3 чанка
        │       └─ пусто → «не знаю» + просьба уточнить (генерация не зовётся)
        ├─ [4] генерация (строгий JSON): в промпте — чанки с chunk_id-паспортами
        │       + окно истории + ПАМЯТЬ ЗАДАЧИ (ограничения обязательны)
        │       → {answer, sources, quotes}
        ├─ [5] пост-валидация источников/цитат + faithfulness-судья (из day-4)
        └─ [6] термины: на вопросы «что такое X?» ответ (усечённый)
                детерминированно попадает в глоссарий памяти

Проверка: scenarios.json — два сценария по 13 сообщений с авточеками
(ветка, разрешение follow-up в rewrite, факты, валидность источников/цитат,
отказы на вне-корпусных вопросах, соблюдение ограничений памяти, asserts
по самой памяти). Артефакт — results/eval_*.json (gitignored).

Запуск:
    python rag_chat.py                    # оба сценария + сводка + артефакт
    python rag_chat.py --scenario 1       # только сценарий 1
    python rag_chat.py --smoke            # 3-ходовый smoke-тест
    python rag_chat.py --chat             # живой интерактивный чат
    python rag_chat.py --k1 10 --k2 4     # другие top-K

Индекс — из day-1 (structured, 42 чанка, intfloat/multilingual-e5-small),
LLM — glm-4.6 (Z.ai coding endpoint). Ключ ZAI_API_KEY хранится в .env
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
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import requests
from dotenv import load_dotenv

# Тихий режим кэша HuggingFace на Windows (как в day-1..day-4).
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_INDEX = BASE_DIR.parent / "day-1" / "index" / "structured.json"
SCENARIOS_FILE = BASE_DIR / "scenarios.json"
RESULTS_DIR = BASE_DIR / "results"

# OpenAI-совместимый coding-эндпоинт Z.ai (тот же, что в day-2..day-4).
ZAI_CHAT_URL_DEFAULT = "https://api.z.ai/api/coding/paas/v4/chat/completions"
ZAI_MODEL_DEFAULT = "glm-4.6"
TEMPERATURE = 0.0
REQUEST_TIMEOUT = 120

K1_DEFAULT = 8            # top-K ДО отбора: сколько кандидатов достаёт поиск
K2_DEFAULT = 3            # top-K ПОСЛЕ отбора: сколько чанков попадает в контекст
LLM_THRESHOLD_DEFAULT = 2     # порог LLM-скора релевантности (0–10) — плато 2–4 из sweep day-3
QUOTE_MATCH_THRESHOLD = 0.90  # порог нечёткого совпадения цитаты с текстом чанка

HISTORY_REWRITE_TURNS = 4   # сколько ходов истории видит rewrite (разрешение местоимений)
HISTORY_ANSWER_TURNS = 6    # сколько ходов истории видит генерация ответа

VERDICTS = ("supported", "partial", "unsupported")
CLARIFY_MARKERS = ("уточни", "переформулируй", "конкретизируй", "уточнение")
# Маркеры «модель сама честно отказалась» — ответ без источников и цитат,
# по смыслу «в базе этого нет» (как model_no_answer в day-4).
OFF_CORPUS_MARKERS = (
    "вне базы", "не знаю", "не могу ответ", "нет релевантн", "нет информации",
    "нет соответствующ", "не содержится", "не удалось найти", "нет данных",
)

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

# --------------------------------------------------------------------------- #
# System-промпты
# --------------------------------------------------------------------------- #

SYSTEM_MEMORY = (
    "Ты — модуль памяти задачи в чате с ассистентом базы знаний курса "
    "(промптинг, агенты, управление контекстом, MCP, RAG). Даны текущая память "
    "задачи, последние ходы диалога и новое сообщение пользователя. Извлеки из "
    "СООБЩЕНИЯ информацию о его задаче и верни строго JSON без "
    "markdown-обёрток и пояснений, точно такой структуры: "
    '{"meta_only": false, "needs_history": false, "goal": null, '
    '"add_clarifications": [], "add_constraints": [], "terms_asked": []}. '
    "Правила: meta_only=true — только если в сообщении НЕТ вопроса или "
    "информационного запроса вовсе (приветствие, формулировка цели, правила "
    "поведения, уточнение условий, благодарность, согласие). Вопрос не по темам "
    "базы (кулинария, спорт и т.п.) — это всё равно вопрос: ставь "
    "meta_only=false, ассистент сам честно откажется после поиска. "
    "needs_history=true — только если ответ буквально содержится в прошлых "
    "сообщениях диалога, то есть вопрос О САМОМ ДИАЛОГЕ («напомни, о чём мы "
    "говорили», «что я выбрал», «что ты тогда сказал»); если же вопрос просит "
    "знания из базы — даже со ссылкой на диалог, итогом или советом («какой "
    "способ лучше выбрать?», «собери всё и посоветуй») — ставь "
    "needs_history=false. goal — цель пользователя одной фразой, только если "
    "она явно сформулирована в этом сообщении, иначе null; "
    "add_clarifications — новые факты об условиях задачи (окружение, "
    "ресурсы, сроки); add_constraints — правила, которых ассистент обязан "
    "придерживаться в ответах (формат, стиль, запреты); terms_asked — термины, "
    "определения которых пользователь просит в этом сообщении: только сам "
    "термин (одно-три слова, как он написан в сообщении), а не весь вопрос. "
    "Все тексты — по-русски, кратко. Ничего не выдумывай и не дублируй то, "
    "что уже есть в памяти."
)

SYSTEM_REWRITE = (
    "Ты — подготовитель поисковых запросов для базы знаний курса по AI "
    "(промптинг, агенты, управление контекстом, MCP, RAG). Даны история "
    "диалога, память задачи и новый вопрос пользователя. Перепиши вопрос в "
    "компактный САМОДОСТАТОЧНЫЙ поисковый запрос к этой базе: разреши "
    "местоимения и отсылки («он», «его», «туда», «это», «мой выбор») через "
    "историю и память задачи; разверни расплывчатые формулировки в конкретные "
    "термины темы; убери разговорные обороты; сохрани информационную "
    "потребность целиком и ничего не добавляй от себя. Ответь только самим "
    "поисковым запросом, одной строкой, без пояснений и кавычек."
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
    "Ты — ассистент с доступом к базе знаний курса. Тебе даны: память задачи "
    "(цель, уточнения, ограничения, термины), последние ходы диалога, "
    "фрагменты базы знаний (первая строка каждого фрагмента — "
    "[chunk_id | документ | раздел]) и вопрос пользователя. Ответь на вопрос "
    "по-русски, ТОЛЬКО на основе фактов из фрагментов; историю и память "
    "используй, чтобы понять контекст вопроса, учесть условия задачи и связать "
    "ответ с предыдущими ходами. ОБЯЗАТЕЛЬНО соблюдай ограничения из памяти "
    "задачи (длина, стиль, запреты). Кратко: 2–6 предложений, если ограничения "
    "не требуют иного. Верни строго JSON без markdown-обёрток и пояснений, "
    "точно такой структуры: "
    '{"answer": "текст ответа", "sources": [{"chunk_id": "…", "doc": "…", '
    '"section": "…"}], "quotes": [{"chunk_id": "…", "text": "…"}]}. '
    "Правила: sources — все фрагменты, из которых взята информация для ответа "
    "(1–3), поля chunk_id/doc/section копируй из первой строки фрагмента "
    "дословно; quotes — 1–3 ДОСЛОВНЫЕ цитаты из этих фрагментов, "
    "подтверждающие ответ (точная копия фрагмента текста, а не пересказ), "
    "каждая со своим chunk_id. Если ответа во фрагментах нет — верни "
    '{"answer": "", "sources": [], "quotes": []}.'
)

SYSTEM_FAITH = (
    "Ты — строгий оценщик обоснованности ответов. Даны вопрос, ответ "
    "ассистента и дословные цитаты из фрагментов базы знаний. Реши, "
    "подтверждается ли смысл ответа этими цитатами: supported — утверждения "
    "ответа выводятся из цитат; partial — часть утверждений подтверждается, "
    "часть нет; unsupported — ответ по сути не выводится из цитат "
    "(галлюцинация или общие знания). Стилистические расхождения, перестановка "
    "слов и отсылки к истории диалога или памяти задачи («как ты говорил», "
    "«в моей ситуации») сами по себе не считаются расхождением — оцени, "
    "подтверждаются ли ФАКТЫ ответа цитатами. Ответь строго JSON без "
    "пояснений: "
    '{"verdict": "supported|partial|unsupported", "reason": "кратко, по-русски"}.'
)

SYSTEM_HISTORY_ANSWER = (
    "Ты — ассистент чата. Вопрос пользователя касается самого диалога или его "
    "задачи («напомни, о чём мы говорили», «что я выбрал»). Даны память задачи "
    "и история диалога. Ответь по-русски, кратко (2–5 предложений) и ТОЛЬКО "
    "на основе истории и памяти; ограничения из памяти задачи обязательны. "
    "Если в истории нет нужного — честно скажи об этом. Верни строго JSON без "
    'markdown-обёрток и пояснений: {"answer": "текст ответа"} — источники из '
    "базы знаний в таком ответе не указываются."
)


# --------------------------------------------------------------------------- #
# Ошибки LLM-вызова и сам вызов (как в day-4)
# --------------------------------------------------------------------------- #


class LlmRetryableError(RuntimeError):
    """Сетевой сбой или 429/5xx — имеет смысл повторить."""


class LlmFatalError(RuntimeError):
    """Ошибка без смысла повторять (нет ключа, 401, кривой запрос)."""


RATE_LIMIT_DELAYS = (10, 20, 40)  # 429 — паузы между попытками, секунды
NETWORK_RETRY_DELAYS = (3, 10, 30)  # сетевые/5xx сбои (в т.ч. DNS) — транзиентны


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
    # 429 (rate limit) — транзиентный: терпеливый backoff 10/20/40 с; сетевые
    # и серверные сбои (обрывы, DNS) тоже транзиентны — 3 попытки с паузами.
    waits_429, net_attempt = 0, 0
    while True:
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
                RUNTIME_STATS["llm_calls"] += 1
                return content
            if resp.status_code == 429:
                if waits_429 >= len(RATE_LIMIT_DELAYS):
                    raise LlmFatalError(
                        f"HTTP 429: rate limit держится после {waits_429} ожиданий: "
                        f"{resp.text[:160]}"
                    )
                wait = RATE_LIMIT_DELAYS[waits_429]
                waits_429 += 1
                print(f"    [rate limit: ждём {wait} с "
                      f"(ожидание {waits_429}/{len(RATE_LIMIT_DELAYS)})]")
                time.sleep(wait)
                continue
            if resp.status_code >= 500:
                raise LlmRetryableError(f"HTTP {resp.status_code}: {resp.text[:160]}")
            raise LlmFatalError(f"HTTP {resp.status_code}: {resp.text[:160]}")
        except LlmFatalError:
            raise
        except (requests.RequestException, LlmRetryableError) as exc:
            if net_attempt >= len(NETWORK_RETRY_DELAYS):
                raise LlmFatalError(
                    f"LLM недоступна после {net_attempt} повторов: {exc}") from exc
            wait = NETWORK_RETRY_DELAYS[net_attempt]
            net_attempt += 1
            print(f"    [сбой запроса, ждём {wait} с "
                  f"(попытка {net_attempt}/{len(NETWORK_RETRY_DELAYS)}): {exc}]")
            time.sleep(wait)


RUNTIME_STATS = {"llm_calls": 0}


# --------------------------------------------------------------------------- #
# Индекс и поиск (как в day-2..day-4)
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
    """Эмбеддер запросов — та же модель, что индексировала чанки."""

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
    """Чанки → контекст для промпта, с паспортом [chunk_id | doc | section]."""
    parts = []
    for c, _, _ in kept:
        parts.append(f"[{c['id']} | {c['doc']} | {heading_of(c)}]\n{c['text']}")
    return "\n\n".join(parts)


def _extract_json(raw: str):
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        return json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return None


# --------------------------------------------------------------------------- #
# Память задачи: цель · уточнения · ограничения · термины
# --------------------------------------------------------------------------- #


def norm_key(s: str) -> str:
    """Нормализация для дедупликации: регистр, ё→е, пунктуация, пробелы."""
    s = s.lower().replace("ё", "е")
    s = re.sub(r"[^\w\s-]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


@dataclass
class TaskMemory:
    goal: str | None = None
    clarifications: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    terms: dict[str, str] = field(default_factory=dict)  # термин → определение

    def render(self) -> str:
        """Память → блок для промпта. Пустая память → пустая строка."""
        if self.is_empty():
            return ""
        lines = []
        if self.goal:
            lines.append(f"Цель пользователя: {self.goal}")
        if self.clarifications:
            lines.append("Уточнения (условия задачи):")
            lines += [f"  - {c}" for c in self.clarifications]
        if self.constraints:
            lines.append("Ограничения (ОБЯЗАТЕЛЬНЫ в каждом ответе):")
            lines += [f"  - {c}" for c in self.constraints]
        if self.terms:
            lines.append("Термины (употребляй в значениях диалога):")
            lines += [f"  - {t} — {d}" for t, d in self.terms.items()]
        return "\n".join(lines)

    def is_empty(self) -> bool:
        return not (self.goal or self.clarifications or self.constraints or self.terms)

    def snapshot(self) -> dict:
        return {
            "goal": self.goal,
            "clarifications": list(self.clarifications),
            "constraints": list(self.constraints),
            "terms": dict(self.terms),
        }


def _clean_str_list(v, max_len: int = 300) -> list[str]:
    if not isinstance(v, list):
        return []
    out = []
    for x in v:
        if isinstance(x, str):
            x = " ".join(x.split()).strip()
            if 2 <= len(x) <= max_len:
                out.append(x)
    return out


def merge_memory(mem: TaskMemory, upd: dict) -> list[str]:
    """Детерминированный merge извлечённого в память. Возвращает список
    человекочитаемых изменений (для показа и логирования)."""
    changes: list[str] = []
    goal = upd.get("goal")
    if isinstance(goal, str) and goal.strip():
        goal = " ".join(goal.split()).strip()[:200]
        if norm_key(goal) != norm_key(mem.goal or ""):
            mem.goal = goal
            changes.append(f"цель: «{goal}»")
    for item in _clean_str_list(upd.get("add_clarifications")):
        if norm_key(item) not in {norm_key(c) for c in mem.clarifications}:
            mem.clarifications.append(item)
            changes.append(f"уточнение: «{item}»")
    for item in _clean_str_list(upd.get("add_constraints")):
        if norm_key(item) not in {norm_key(c) for c in mem.constraints}:
            mem.constraints.append(item)
            changes.append(f"ограничение: «{item}»")
    return changes


def extract_memory(text: str, session: "ChatSession") -> dict:
    """[0] Один LLM-вызов: классификация сообщения + извлечение памяти."""
    mem_block = session.memory.render() or "— память пуста —"
    user = (
        f"ПАМЯТЬ ЗАДАЧИ (текущее состояние):\n{mem_block}\n\n"
        f"ПОСЛЕДНИЕ ХОДЫ ДИАЛОГА:\n{session.window_str(HISTORY_REWRITE_TURNS, 260)}\n\n"
        f"СООБЩЕНИЕ ПОЛЬЗОВАТЕЛЯ:\n{text}"
    )
    messages = [
        {"role": "system", "content": SYSTEM_MEMORY},
        {"role": "user", "content": user},
    ]
    default = {
        "meta_only": False, "needs_history": False, "goal": None,
        "add_clarifications": [], "add_constraints": [], "terms_asked": [],
    }
    for attempt in (1, 2):
        raw = llm_call(messages)
        data = _extract_json(raw)
        if isinstance(data, dict):
            return {
                "meta_only": bool(data.get("meta_only", False)),
                "needs_history": bool(data.get("needs_history", False)),
                "goal": data.get("goal"),
                "add_clarifications": _clean_str_list(data.get("add_clarifications")),
                "add_constraints": _clean_str_list(data.get("add_constraints")),
                "terms_asked": [
                    t for t in _clean_str_list(data.get("terms_asked"), 60)
                    if len(t.split()) <= 3  # термин, а не весь вопрос
                ],
            }
        print(f"    [память: JSON не разобран, попытка {attempt}]")
    print("    [память: извлечение не удалось, работаем без обновления]")
    return default


def build_ack_answer(changes: list[str], upd: dict) -> str:
    """Ответ-подтверждение на meta-only сообщение (детерминированно, без LLM)."""
    bits: list[str] = []
    if any(c.startswith("цель") for c in changes):
        bits.append("цель зафиксирована")
    if upd.get("add_constraints"):
        bits.append("ограничения учту: " + "; ".join(
            " ".join(c.split())[:80] for c in upd["add_constraints"]))
    if upd.get("add_clarifications"):
        bits.append("уточнения приняты")
    if not bits:
        return "Хорошо, я вас услышал. Продолжаем."
    tail = ". ".join(bits) + "."
    return "Принято. " + tail[0].upper() + tail[1:]


# --------------------------------------------------------------------------- #
# Шаги RAG-хода: rewrite → реранк → генерация → валидация → судья
# --------------------------------------------------------------------------- #


def rewrite_query(question: str, session: "ChatSession") -> str:
    """[1] Rewrite с историей и памятью: follow-up → самодостаточный запрос."""
    mem_block = session.memory.render() or "— память пуста —"
    user = (
        f"ПАМЯТЬ ЗАДАЧИ:\n{mem_block}\n\n"
        f"ИСТОРИЯ ДИАЛОГА:\n{session.window_str(HISTORY_REWRITE_TURNS, 240)}\n\n"
        f"НОВЫЙ ВОПРОС ПОЛЬЗОВАТЕЛЯ:\n{question}"
    )
    raw = llm_call([
        {"role": "system", "content": SYSTEM_REWRITE},
        {"role": "user", "content": user},
    ])
    rewritten = " ".join(raw.split()).strip('"«»„“').strip()
    if not (3 <= len(rewritten) <= 300):  # защита от мусора — ищем исходным
        rewritten = question
    return rewritten


def parse_scores(raw: str, n: int) -> list[float] | None:
    """Ответ судьи-реранкера → список из n скоров 0–10."""
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


def rerank_scores(query: str, candidates: list[tuple[dict, float]]) -> list[float] | None:
    """[3] Один батч-вызов LLM: скор релевантности 0–10 каждому кандидату.

    Реранкуется САМОДОСТАТОЧНЫЙ rewrite-запрос: судья не должен гадать, что
    значит «а если я перееду с ним на VPS».
    """
    blocks = []
    for i, (c, _) in enumerate(candidates, start=1):
        text = " ".join(c["text"].split())[:400]
        blocks.append(f"[{i}] ({c['doc']} › {heading_of(c)})\n{text}")
    user = f"Вопрос: {query}\n\nОтрывки:\n\n" + "\n\n".join(blocks)
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
    if scores is None:
        ranked = [(c, cos, None) for c, cos in candidates]
        return ranked[:k2], True
    ranked = [(c, cos, s) for (c, cos), s in zip(candidates, scores)]
    ranked.sort(key=lambda t: (-t[2], -t[1]))
    kept = [item for item in ranked if item[2] >= llm_threshold][:k2]
    return kept, False


def build_hints(cands: list[tuple[dict, float]], scores: list[float] | None) -> list[str]:
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


def parse_generation(raw: str) -> dict | None:
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


def generate_structured(question: str, session: "ChatSession", kept: list[tuple]) -> tuple[dict, str]:
    """[4] Генерация: чанки + окно истории + память задачи → строгий JSON."""
    mem_block = session.memory.render() or "— память пуста —"
    user = (
        f"ПАМЯТЬ ЗАДАЧИ:\n{mem_block}\n\n"
        f"ПОСЛЕДНИЕ ХОДЫ ДИАЛОГА:\n{session.window_str(HISTORY_ANSWER_TURNS, 340)}\n\n"
        f"ФРАГМЕНТЫ БАЗЫ ЗНАНИЙ:\n\n{build_context(kept)}\n\n"
        f"ВОПРОС ПОЛЬЗОВАТЕЛЯ: {question}"
    )
    messages = [
        {"role": "system", "content": SYSTEM_ANSWER},
        {"role": "user", "content": user},
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


def history_answer(question: str, session: "ChatSession") -> str:
    """[история] Ответ на вопрос о самом диалоге — из истории и памяти."""
    mem_block = session.memory.render() or "— память пуста —"
    user = (
        f"ПАМЯТЬ ЗАДАЧИ:\n{mem_block}\n\n"
        f"ИСТОРИЯ ДИАЛОГА (полная):\n{session.window_str(len(session.history), 320)}\n\n"
        f"ВОПРОС ПОЛЬЗОВАТЕЛЯ: {question}"
    )
    messages = [
        {"role": "system", "content": SYSTEM_HISTORY_ANSWER},
        {"role": "user", "content": user},
    ]
    for attempt in (1, 2):
        raw = llm_call(messages)
        data = _extract_json(raw)
        if isinstance(data, dict) and isinstance(data.get("answer"), str) and data["answer"].strip():
            return data["answer"].strip()
        print(f"    [история-ответ: JSON не разобран, попытка {attempt}]")
    return " ".join(raw.split())  # откат: сырой текст как ответ


# --------------------------------------------------------------------------- #
# Пост-валидация источников/цитат + faithfulness-судья (из day-4)
# --------------------------------------------------------------------------- #


def norm_text(s: str) -> str:
    s = s.lower().replace("ё", "е")
    s = re.sub(r"[«»„“”\"'`]", " ", s)
    s = re.sub(r"[—–−]", "-", s)
    return re.sub(r"\s+", " ", s).strip()


def fuzzy_contains(quote: str, text: str, threshold: float = QUOTE_MATCH_THRESHOLD) -> dict:
    q, t = norm_text(quote), norm_text(text)
    if len(q) < 8:
        return {"valid": False, "ratio": 0.0, "reason": "цитата пустая или слишком короткая"}
    if q in t:
        return {"valid": True, "ratio": 1.0, "reason": "дословное вхождение"}
    best = 0.0
    step = max(1, len(q) // 8)
    win = len(q) + step
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


def faithfulness_verdict(
    question: str, answer: str, valid_quotes: list[dict], by_id: dict[str, dict]
) -> dict:
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


def check_facts(answer: str, expected_facts: list[dict]) -> list[dict]:
    low = answer.lower()
    return [
        {"fact": f["fact"], "ok": any(v.lower() in low for v in f["any_of"])}
        for f in expected_facts
    ]


def looks_off_corpus_refusal(answer: str) -> bool:
    """Похож ли ответ на честный отказ «в базе этого нет» (без источников)."""
    low = answer.lower()
    return any(m in low for m in OFF_CORPUS_MARKERS)


# --------------------------------------------------------------------------- #
# Чат-сессия: история + память + полный ход
# --------------------------------------------------------------------------- #


class Runtime:
    def __init__(self, index: dict, embedder: QueryEmbedder, k1: int, k2: int, threshold: float):
        self.index = index
        self.embedder = embedder
        self.k1, self.k2, self.threshold = k1, k2, threshold


class ChatSession:
    def __init__(self, rtx: Runtime):
        self.rtx = rtx
        self.history: list[dict] = []  # ходы: {role, text, kind, sources, quotes}
        self.memory = TaskMemory()

    # -- история ---------------------------------------------------------- #

    def add(self, role: str, text: str, kind: str, sources=None, quotes=None) -> None:
        self.history.append({
            "role": role, "text": text, "kind": kind,
            "sources": sources or [], "quotes": quotes or [],
        })

    def window_str(self, n_turns: int, char_limit: int = 320) -> str:
        turns = self.history[-n_turns:] if n_turns > 0 else self.history
        lines = []
        for t in turns:
            who = "Пользователь" if t["role"] == "user" else "Ассистент"
            text = " ".join(t["text"].split())
            if len(text) > char_limit:
                text = text[: char_limit - 1] + "…"
            lines.append(f"{who}: {text}")
        return "\n".join(lines) if lines else "— диалог только начался —"

    # -- полный ход -------------------------------------------------------- #

    def handle_user_message(self, text: str) -> dict:
        self.add("user", text, "user")
        r: dict = {"question": text}

        # [0] память задачи: классификация + извлечение (до ответа — чтобы
        # ограничения из ЭТОГО сообщения действовали уже на ЭТОТ ответ)
        upd = extract_memory(text, self)
        changes = merge_memory(self.memory, upd)
        r["memory_changes"] = changes
        r["memory_upd"] = {k: upd[k] for k in ("goal", "add_clarifications",
                                               "add_constraints", "terms_asked")}

        # ветка 1: сообщение без вопроса к базе (правила/цель/приветствие)
        if upd["meta_only"]:
            answer = build_ack_answer(changes, upd)
            self.add("assistant", answer, "ack")
            r.update({"kind": "ack", "answer": answer, "refusal": False,
                      "clarification": False, "rewritten": None,
                      "sources": [], "quotes": [], "faithfulness": None})
            return r

        # ветка 2: вопрос о самом диалоге → ответ из истории и памяти
        if upd["needs_history"]:
            answer = history_answer(text, self)
            self.add("assistant", answer, "history")
            r.update({"kind": "history", "answer": answer, "refusal": False,
                      "clarification": False, "rewritten": None,
                      "sources": [], "quotes": [], "faithfulness": None})
            return r

        # ветка 3: обычный RAG-ход
        rewritten = rewrite_query(text, self)
        r["rewritten"] = rewritten
        cands = vector_search(self.rtx.index["chunks"],
                              self.rtx.embedder.embed_query(rewritten), self.rtx.k1)
        scores = rerank_scores(rewritten, cands)
        kept, fallback = apply_stage2(cands, scores, self.rtx.k2, self.rtx.threshold)
        hints = build_hints(cands, scores)
        by_id = {c["id"]: c for c, _, _ in kept}
        r.update({
            "n_candidates": len(cands),
            "candidates": [{"chunk_id": c["id"], "doc": c["doc"],
                            "heading": heading_of(c), "cos": round(cos, 3),
                            "llm_score": s if s is None else round(s, 1)}
                           for (c, cos), s in zip(cands, scores or [None] * len(cands))],
            "max_llm_score": max(scores) if scores else None,
            "n_kept": len(kept),
            "kept": [{"chunk_id": c["id"], "doc": c["doc"], "heading": heading_of(c),
                      "llm_score": s if s is None else round(s, 1)}
                     for c, _, s in kept],
            "fallback": fallback,
        })

        # порог не прошёл никто → детерминированный отказ
        if not kept:
            final = (REFUSAL_EMPTY_CTX_WITH_HINTS.format(hints=" · ".join(hints))
                     if hints else REFUSAL_EMPTY_CTX_PLAIN)
            self.add("assistant", final, "refusal")
            r.update({"kind": "refusal", "answer": final, "refusal": True,
                      "refusal_kind": "empty_context",
                      "clarification": any(m in final.lower() for m in CLARIFY_MARKERS),
                      "sources": [], "quotes": [], "faithfulness": None})
            return r

        # генерация → валидация → (отказ модели? → судья?) 
        parsed, raw = generate_structured(text, self, kept)
        sources_v = validate_sources(parsed["sources"], by_id)
        quotes_v = validate_quotes(parsed["quotes"], by_id)
        valid_quotes = [q for q in quotes_v if q["valid"]]
        valid_sources = [s for s in sources_v if s["valid"]]

        # Модель сама сказала «в базе нет»: пустой ответ ИЛИ ответ без источников
        # и цитат с маркерами честного отказа (day-4: model_no_answer) →
        # заменяем на канонический отказ, судьёй не проверяем.
        model_no_answer = (not parsed["answer"]) or (
            not valid_quotes and not valid_sources
            and looks_off_corpus_refusal(parsed["answer"])
        )

        if model_no_answer:
            final = (REFUSAL_EMPTY_CTX_WITH_HINTS.format(hints=" · ".join(hints))
                     if hints else REFUSAL_EMPTY_CTX_PLAIN)
            self.add("assistant", final, "refusal")
            r.update({"kind": "refusal", "answer": final, "refusal": True,
                      "refusal_kind": "model_no_answer",
                      "clarification": any(m in final.lower() for m in CLARIFY_MARKERS),
                      "sources": [], "quotes": [], "faithfulness": None,
                      "answer_original": parsed["answer"] or None})
            return r

        faith = faithfulness_verdict(text, parsed["answer"], valid_quotes, by_id)

        final, refusal, refusal_kind = parsed["answer"], False, None
        if faith["verdict"] == "unsupported":
            final = REFUSAL_UNFAITHFUL
            refusal, refusal_kind = True, "unfaithful"

        # [6] термины: «что такое X» → глоссарий (детерминированно из ответа)
        terms_captured = []
        if upd["terms_asked"] and final and not refusal:
            for term in upd["terms_asked"]:
                definition = " ".join(final.split())[:240]
                key = " ".join(term.split())
                self.memory.terms[key] = definition
                terms_captured.append(key)

        self.add("assistant", final, "rag",
                 sources=[s for s in sources_v if s["valid"]],
                 quotes=[q for q in quotes_v if q["valid"]])
        r.update({
            "kind": "rag", "answer": final, "refusal": refusal,
            "refusal_kind": refusal_kind,
            "clarification": any(m in final.lower() for m in CLARIFY_MARKERS),
            "generation_parse_ok": parsed["parse_ok"],
            "sources": sources_v, "quotes": quotes_v,
            "faithfulness": faith,
            "answer_original": parsed["answer"] if refusal else None,
            "terms_captured": terms_captured,
        })
        return r


# --------------------------------------------------------------------------- #
# Сценарии: авточеки
# --------------------------------------------------------------------------- #


def memory_assert_rows(ma: dict, mem: TaskMemory) -> list[tuple[str, bool]]:
    rows = []
    goal = (mem.goal or "").lower()
    for sub in ma.get("goal_contains", []):
        rows.append((f"цель ∋ «{sub}»", sub.lower() in goal))
    if "constraints_min" in ma:
        n = ma["constraints_min"]
        rows.append((f"ограничений ≥ {n}", len(mem.constraints) >= n))
    if "clarifications_min" in ma:
        n = ma["clarifications_min"]
        rows.append((f"уточнений ≥ {n}", len(mem.clarifications) >= n))
    blob = " || ".join(mem.constraints).lower()
    for sub in ma.get("constraints_contain", []):
        rows.append((f"ограничения ∋ «{sub}»", sub.lower() in blob))
    terms_blob = " || ".join(f"{k}: {v}" for k, v in mem.terms.items()).lower()
    for sub in ma.get("terms_include", []):
        rows.append((f"термины ∋ «{sub}»", sub.lower() in terms_blob))
    return rows


def eval_checks(msg: dict, r: dict, session: ChatSession) -> list[tuple[str, bool]]:
    """Авточеки одного сообщения → список (метка, выполнено)."""
    rows: list[tuple[str, bool]] = []
    expect = msg.get("expect", "rag")
    rows.append((f"ветка {expect}", r["kind"] == expect))
    rw = (r.get("rewritten") or "").lower()
    for sub in msg.get("rewrite_contains", []):
        rows.append((f"rewrite ∋ «{sub}»", sub.lower() in rw))
    any_list = msg.get("rewrite_contains_any", [])
    if any_list:
        rows.append(("rewrite ∋ " + "|".join(any_list),
                     any(s.lower() in rw for s in any_list)))
    if expect == "refusal":
        rows.append(("честный отказ", r["kind"] == "refusal"))
        rows.append(("просит уточнить", bool(r.get("clarification"))))
    answer = r.get("answer") or ""
    if msg.get("expected_facts") and r["kind"] in ("rag", "history"):
        facts = check_facts(answer, msg["expected_facts"])
        ok = sum(f["ok"] for f in facts)
        rows.append((f"факты {ok}/{len(facts)}", ok == len(facts)))
    if msg.get("no_code") and r["kind"] in ("rag", "history"):
        rows.append(("без кода (``` нет)", "```" not in answer))
    ma = msg.get("memory_asserts")
    if ma:
        rows.extend(memory_assert_rows(ma, session.memory))
    return rows


def short(text: str, limit: int = 110) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def print_turn(msg: dict, r: dict, rows: list[tuple[str, bool]]) -> None:
    print(f"\n  {msg['id']} [{r['kind']}] «{short(msg['text'], 96)}»")
    if r["kind"] == "ack":
        if r["memory_changes"]:
            for ch in r["memory_changes"]:
                print(f"      память: +{ch}")
        print(f"      ответ: «{short(r['answer'], 130)}»")
    elif r["kind"] == "history":
        print("      вопрос о диалоге → ответ из истории и памяти (поиск по базе не нужен)")
        print(f"      ответ: «{short(r['answer'], 160)}»")
    elif r["kind"] == "refusal":
        if r.get("rewritten"):
            print(f"      rewrite → «{short(r['rewritten'], 80)}»")
        if r.get("refusal_kind") == "model_no_answer":
            print(f"      отбор {r.get('n_kept', '?')}/{r.get('n_candidates', '?')} → "
                  f"модель честно сказала «в базе нет» → канонический отказ")
        else:
            ms = r.get("max_llm_score")
            print(f"      отбор 0/{r['n_candidates']} (макс. скор судьи "
                  f"{ms if ms is not None else '—'} < порога) → генерация не вызывается")
        print(f"      ответ: «{short(r['answer'], 140)}»")
    else:  # rag
        print(f"      rewrite → «{short(r['rewritten'], 80)}»")
        kept_line = ", ".join(f"{k['chunk_id']} ({k['llm_score']:g})" for k in r["kept"])
        print(f"      отбор {r['n_kept']}/{r['n_candidates']} в контекст: {kept_line}")
        if r["memory_changes"]:
            for ch in r["memory_changes"]:
                print(f"      память: +{ch}")
        if r.get("terms_captured"):
            print(f"      глоссарий: {', '.join(r['terms_captured'])}")
        sv = sum(s["valid"] for s in r["sources"])
        qv = sum(q["valid"] for q in r["quotes"])
        faith = r["faithfulness"]["verdict"] if r["faithfulness"] else "—"
        print(f"      ответ: «{short(r['answer'], 150)}»")
        line = (f"      источники {sv}/{len(r['sources'])} · цитаты "
                f"{qv}/{len(r['quotes'])} · смысл: {faith}")
        if r["refusal"]:
            line += f" → ОТВЕРГНУТ ({r['refusal_kind']})"
        print(line)
        for s in r["sources"]:
            if s["valid"]:
                print(f"        источник ✓ {s['doc']} › {s['section']} [{s['chunk_id']}]")
        for q in r["quotes"]:
            if q["valid"]:
                print(f"        цитата   ✓ «{short(q['text'], 84)}» "
                      f"[{q['chunk_id']}] ({q['ratio']:.2f})")
    ok = sum(1 for _, v in rows if v)
    marks = " · ".join(("✓" if v else "✗") + " " + lbl for lbl, v in rows)
    print(f"      чеки {ok}/{len(rows)}: {marks}")


def print_memory_snapshot(name: str, mem: TaskMemory) -> None:
    print(f"\n  Память задачи после сценария «{name}»:")
    print(f"    Цель: {mem.goal or '—'}")
    print(f"    Уточнения: " + ("; ".join(mem.clarifications) if mem.clarifications else "—"))
    print(f"    Ограничения: " + ("; ".join(mem.constraints) if mem.constraints else "—"))
    if mem.terms:
        print("    Термины:")
        for t, d in mem.terms.items():
            print(f"      {t} — {short(d, 100)}")
    else:
        print("    Термины: —")


def summarize_scenario(msgs: list[dict], results: list[dict]) -> dict:
    kinds = Counter(r["kind"] for r in results)
    rag = [r for r in results if r["kind"] == "rag"]
    ref = [r for r in results if r["kind"] == "refusal"]
    hist = [r for r in results if r["kind"] == "history"]

    facts_ok = facts_total = 0
    facts_msgs_all = facts_msgs_total = 0
    for m, r in zip(msgs, results):
        if m.get("expected_facts") and r["kind"] in ("rag", "history"):
            facts = check_facts(r["answer"], m["expected_facts"])
            facts_ok += sum(f["ok"] for f in facts)
            facts_total += len(facts)
            facts_msgs_all += all(f["ok"] for f in facts)
            facts_msgs_total += 1

    def verdict(v: str) -> int:
        return sum(1 for r in rag if r["faithfulness"] and r["faithfulness"]["verdict"] == v)

    sources_total = sum(len(r["sources"]) for r in rag)
    sources_valid = sum(sum(s["valid"] for s in r["sources"]) for r in rag)
    quotes_total = sum(len(r["quotes"]) for r in rag)
    quotes_valid = sum(sum(q["valid"] for q in r["quotes"]) for r in rag)

    rows = [row for m, r in zip(msgs, results) for row in r["_checks"]]
    rewrite_rows = [v for lbl, v in rows if lbl.startswith("rewrite")]
    memory_rows = [v for lbl, v in rows if lbl.startswith(("цель", "огранич", "уточнен", "термины"))]
    nocde_rows = [v for lbl, v in rows if lbl.startswith("без кода")]
    branch_rows = [v for lbl, v in rows if lbl.startswith("ветка")]

    return {
        "n_messages": len(msgs),
        "kinds": dict(kinds),
        "facts_ok": facts_ok, "facts_total": facts_total,
        "facts_msgs_all": facts_msgs_all, "facts_msgs_total": facts_msgs_total,
        "rewrite_ok": sum(rewrite_rows), "rewrite_total": len(rewrite_rows),
        "memory_ok": sum(memory_rows), "memory_total": len(memory_rows),
        "no_code_ok": sum(nocde_rows), "no_code_total": len(nocde_rows),
        "branch_ok": sum(branch_rows), "branch_total": len(branch_rows),
        "checks_ok": sum(1 for _, v in rows if v), "checks_total": len(rows),
        "sources_total": sources_total, "sources_valid": sources_valid,
        "quotes_total": quotes_total, "quotes_valid": quotes_valid,
        "supported": verdict("supported"), "partial": verdict("partial"),
        "unsupported": verdict("unsupported"),
        "refusals_honest": sum(1 for m, r in zip(msgs, results)
                               if m.get("expect") == "refusal" and r["kind"] == "refusal"),
        "refusals_expected": sum(1 for m in msgs if m.get("expect") == "refusal"),
        "refusals_clarify": sum(1 for m, r in zip(msgs, results)
                                if m.get("expect") == "refusal" and r.get("clarification")),
        "history_answers": len(hist),
    }


def run_scenario(scenario: dict, rtx: Runtime) -> tuple[dict, dict, TaskMemory]:
    session = ChatSession(rtx)
    msgs = scenario["messages"]
    results = []
    for msg in msgs:
        r = session.handle_user_message(msg["text"])
        r["_checks"] = eval_checks(msg, r, session)
        results.append(r)
        print_turn(msg, r, r["_checks"])
    summary = summarize_scenario(msgs, results)
    print_memory_snapshot(scenario["name"], session.memory)
    snapshot = session.memory.snapshot()
    # _checks не пишем в артефакт дважды — они уже в summary
    for r in results:
        r.pop("_checks", None)
        r.pop("memory_upd", None)
    return {"name": scenario["name"], "summary": summary,
            "memory": snapshot, "results": results}, summary, session.memory


def print_overall(summaries: list[dict]) -> None:
    agg = Counter()
    kinds = Counter()
    for s in summaries:
        kinds.update(s.get("kinds", {}))
        agg.update({k: v for k, v in s.items() if isinstance(v, (int, float))})
    n_msgs = agg["n_messages"]
    print("\n" + "=" * 78)
    print("[3] Итог — два сценария: история, RAG и память задачи")
    print("=" * 78)
    print(f"    сообщений: {n_msgs} "
          f"(RAG {kinds['rag']} · мета {kinds['ack']} · вне базы {kinds['refusal']} · "
          f"из истории {kinds['history']})\n")
    rows = [
        ("все авточеки (ветка/rewrite/факты/память/код)",
         f"{agg['checks_ok']}/{agg['checks_total']}"),
        ("фактов подтверждено",
         f"{agg['facts_ok']}/{agg['facts_total']} · вопросов со всеми фактами "
         f"{agg['facts_msgs_all']}/{agg['facts_msgs_total']}"),
        ("follow-up: rewrite самодостаточен",
         f"{agg['rewrite_ok']}/{agg['rewrite_total']}"),
        ("источники валидны",
         f"{agg['sources_valid']}/{agg['sources_total']} · ПОДДЕЛОК "
         f"{agg['sources_total'] - agg['sources_valid']}"),
        ("цитаты валидны (дословно)",
         f"{agg['quotes_valid']}/{agg['quotes_total']} · ГАЛЮЦИНАЦИЙ "
         f"{agg['quotes_total'] - agg['quotes_valid']}"),
        ("смысл: supported/partial/unsupported",
         f"{agg['supported']}/{agg['partial']}/{agg['unsupported']}"),
        ("вне базы: отказ + просьба уточнить",
         f"{agg['refusals_honest']}/{agg['refusals_expected']} · "
         f"уточнение {agg['refusals_clarify']}/{agg['refusals_expected']}"),
        ("вопросы о диалоге → ответ из истории", f"{agg['history_answers']}"),
        ("ограничение «без кода» соблюдено",
         f"{agg['no_code_ok']}/{agg['no_code_total']} · нарушений "
         f"{agg['no_code_total'] - agg['no_code_ok']}"),
        ("память задачи: asserts",
         f"{agg['memory_ok']}/{agg['memory_total']}"),
        ("ветвление мета/история/RAG",
         f"{agg['branch_ok']}/{agg['branch_total']}"),
        ("LLM-вызовов", f"{RUNTIME_STATS['llm_calls']}"),
    ]
    for label, value in rows:
        print(f"  {label:<42} {value}")


# --------------------------------------------------------------------------- #
# Интерактивный чат (--chat)
# --------------------------------------------------------------------------- #


def chat_loop(rtx: Runtime) -> None:
    print("\nЖивой чат. Команды: /mem — память · /history — история · "
          "/reset — новая сессия · /exit — выход.")
    session = ChatSession(rtx)
    while True:
        try:
            line = input("\nВы: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not line:
            continue
        cmd = line.lower()
        if cmd in ("/exit", "/quit", "/выход"):
            break
        if cmd == "/mem":
            print("Память задачи:")
            print(session.memory.render() or "  — пуста —")
            continue
        if cmd == "/history":
            print("История:")
            print(session.window_str(len(session.history), 200))
            continue
        if cmd == "/reset":
            session = ChatSession(rtx)
            print("Новая сессия: история и память очищены.")
            continue
        r = session.handle_user_message(line)
        print(f"\nАссистент: {r['answer']}")
        if r["kind"] == "rag":
            sv = [s for s in r["sources"] if s["valid"]]
            if sv:
                print("  Источники: " + "; ".join(
                    f"{s['doc']} › {s['section']} [{s['chunk_id']}]" for s in sv))
            qv = [q for q in r["quotes"] if q["valid"]]
            for q in qv:
                print(f"  Цитата: «{short(q['text'], 100)}» [{q['chunk_id']}]")
        if r["memory_changes"]:
            print("  Память: " + "; ".join(f"+{c}" for c in r["memory_changes"]))
    print("\nВыход из чата.")


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #

SMOKE_SCENARIO = {
    "name": "smoke",
    "messages": [
        {"id": "SM-1", "text": "Привет, я готовлюсь к экзамену по RAG. Что такое RAG?",
         "expect": "rag", "rewrite_contains": ["rag"],
         "memory_asserts": {"goal_contains": ["rag"]}},
        {"id": "SM-2", "text": "Кстати, отвечай дальше коротко и без кода.",
         "expect": "ack", "memory_asserts": {"constraints_min": 1}},
        {"id": "SM-3", "text": "А зачем документы делить на чанки?",
         "expect": "rag", "no_code": True, "rewrite_contains": ["чанк"]},
    ],
}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Week 5 Day 5 — RAG-чат с историей и памятью задачи"
    )
    parser.add_argument("--chat", action="store_true", help="интерактивный чат")
    parser.add_argument("--smoke", action="store_true", help="3-ходовый smoke-тест")
    parser.add_argument("--scenario", type=int, help="номер сценария (1/2)")
    parser.add_argument("--index", default=str(DEFAULT_INDEX), help="путь к индексу day-1")
    parser.add_argument("--k1", type=int, default=K1_DEFAULT, help="top-K ДО отбора")
    parser.add_argument("--k2", type=int, default=K2_DEFAULT, help="top-K ПОСЛЕ отбора")
    parser.add_argument("--threshold", type=float, default=LLM_THRESHOLD_DEFAULT,
                        help="порог LLM-скора релевантности (0–10)")
    args = parser.parse_args()

    load_dotenv(BASE_DIR / ".env")

    print("Week 5 · Day 5 — RAG-чат с историей и памятью задачи "
          "(история → rewrite → RAG → источники + память: цель/уточнения/"
          "ограничения/термины)")

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
    print(f"    top-K: {args.k1} → {args.k2} · порог релевантности: {args.threshold:g} · "
          f"история: rewrite {HISTORY_REWRITE_TURNS} ходов · ответ {HISTORY_ANSWER_TURNS} ходов")

    embedder = QueryEmbedder(emb_meta["model"])
    rtx = Runtime(index, embedder, args.k1, args.k2, args.threshold)

    if args.chat:
        chat_loop(rtx)
        return

    if args.smoke:
        print("\n" + "=" * 78)
        print("[2] Smoke-сценарий (3 сообщения)")
        print("=" * 78)
        run_scenario(SMOKE_SCENARIO, rtx)
        print(f"\nГотово. LLM-вызовов: {RUNTIME_STATS['llm_calls']}")
        return

    if not SCENARIOS_FILE.exists():
        sys.exit(f"[ОШИБКА] Файл сценариев не найден: {SCENARIOS_FILE}")
    scenarios = json.loads(SCENARIOS_FILE.read_text(encoding="utf-8"))["scenarios"]
    if args.scenario:
        scenarios = [s for s in scenarios if s.get("id") == args.scenario]
        if not scenarios:
            sys.exit(f"[ОШИБКА] Сценарий {args.scenario} не найден в {SCENARIOS_FILE}")

    artifacts = []
    summaries = []
    for i, sc in enumerate(scenarios, start=1):
        print("\n" + "=" * 78)
        print(f"[2.{i}] Сценарий: «{sc['name']}» ({len(sc['messages'])} сообщений)")
        print(f"      {sc['description']}")
        print("=" * 78)
        artifact, summary, _ = run_scenario(sc, rtx)
        artifacts.append(artifact)
        summaries.append(summary)

    # Артефакт сохраняем ДО печати сводки: даже сбой печати не потеряет данные.
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
                    "history_rewrite_turns": HISTORY_REWRITE_TURNS,
                    "history_answer_turns": HISTORY_ANSWER_TURNS,
                    "llm_calls": RUNTIME_STATS["llm_calls"],
                },
                "scenarios": artifacts,
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )

    print_overall(summaries)
    print(f"\n  Результаты сохранены: {out}")
    print("\nГотово.")


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    main()
