#!/usr/bin/env python3
"""
hive_kb.py

功能：
- 蜂巢群体经验知识库：SQLite + WAL + FTS5 + 4096 维 float32 embedding 召回
- 适合给 Hermes / 蚁后在派单、复盘、注入 prompt 前做经验检索

4 个 API：
- add(...) -> int：写入一条经验记忆
- query(...) -> list[dict]：FTS5 + embedding 混合召回并重排
- touch(mem_id) -> None：调用方确认使用后增加引用计数
- decay() -> None：更新衰减分并清理过期/低价值记忆

集成点：
- get_embedding(text)：已抽出为本文件内函数，不依赖 hive_pheromones
- format_injection(memories)：TODO，格式化为可注入 prompt 的 markdown 块
- extract_experience(sub_hermes_output)：TODO，1.4 再实现
"""

# === hermes-hive path bootstrap ===
import os
_HERMES_HOME = os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")
_HIVE_DIR = os.path.join(_HERMES_HOME, "hive")
if _HIVE_DIR not in sys.path:
    sys.path.insert(0, _HIVE_DIR)
if _HERMES_HOME not in sys.path:
    sys.path.insert(0, _HERMES_HOME)
# === end bootstrap ===


from __future__ import annotations

# HIVE-PATCH-1.4 蚁后审: 副后没 load .env, 直接 python 调时 env 空
# 加 load_dotenv 让 hive 独立跑也能用
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes")), ".hermes/.env"))
except ImportError:
    pass

import array
import hashlib
import json
import math
import os
import re
import sqlite3
import time
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Iterable, Sequence


DB_PATH = Path(os.environ.get("HERMES_HOME", "~/.hermes")) / ".hermes/hive/hive_kb.db"
EMBEDDING_DIM = 4096
MAX_TASK_TEXT_LEN = 5000
EXPIRE_DAYS = 90
SECONDS_PER_DAY = 86400.0
EXTRACT_MODEL = os.getenv("HIVE_EXTRACT_MODEL", "MiniMax-Text-01")
# HIVE-PATCH-1.4 蚁后审: 副后默认 localhost:8642 Gateway API Server，但 Gateway 不一定启
# 改默认走 minimax-cn 直连，Key 从 MINIMAX_CN_API_KEY 读
EXTRACT_BASE_URL = os.getenv("HIVE_EXTRACT_BASE_URL", "https://api.minimaxi.com/v1")
EXTRACT_API_KEY = os.getenv("HIVE_EXTRACT_API_KEY") or os.getenv("MINIMAX_CN_API_KEY") or os.getenv("MINIMAX_API_KEY") or ""

# HIVE-PATCH-1.4 蚁后审: 4.5s 不够（8000 token prompt + 800 token 答要 6-10s）
EXTRACT_TIMEOUT = float(os.getenv("HIVE_EXTRACT_TIMEOUT", "25"))


ROOT_CAUSES = {
    "data_source",
    "tool_call",
    "network",
    "timeout",
    "resource",
    "llm_parse",
    "impossible",
    "dispatch_mismatch",
    "unknown",
}
STAGES = {"dispatch", "run", "tool", "finalize", "extract"}
EXPERIENCE_FIELDS = (
    "problem",
    "outcome_reason",
    "action",
    "anti_pattern",
    "validation",
    "scope",
)
ATTRIBUTION_FIELDS = ("root_cause", "stage", "confidence")



def _now() -> float:
    return time.time()


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_type TEXT NOT NULL,
                task_text TEXT NOT NULL,
                huluwa_id INTEGER NOT NULL,
                embedding BLOB,
                experience TEXT NOT NULL,
                tools_used TEXT,
                duration_ms INTEGER,
                success INTEGER NOT NULL,
                fail_reason TEXT,
                use_count INTEGER DEFAULT 0,
                last_used_at REAL,
                decay_score REAL DEFAULT 1.0,
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts
            USING fts5(task_text, experience, fail_reason, fts_text)
            """
        )
        _migrate_schema(conn)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_task_type ON memories(task_type)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_decay_score ON memories(decay_score)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_use_count ON memories(use_count)")


def _table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]


def _add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    if column not in _table_columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def _migrate_schema(conn: sqlite3.Connection) -> None:
    for field in EXPERIENCE_FIELDS:
        _add_column_if_missing(conn, "memories", field, f"{field} TEXT DEFAULT ''")
    _add_column_if_missing(conn, "memories", "root_cause", "root_cause TEXT DEFAULT 'unknown'")
    _add_column_if_missing(conn, "memories", "stage", "stage TEXT DEFAULT 'run'")
    _add_column_if_missing(conn, "memories", "confidence", "confidence REAL DEFAULT 0.0")

    if "fts_text" not in _table_columns(conn, "memories_fts"):
        conn.execute("DROP TABLE IF EXISTS memories_fts")
        conn.execute(
            """
            CREATE VIRTUAL TABLE memories_fts
            USING fts5(task_text, experience, fail_reason, fts_text)
            """
        )
        _rebuild_fts(conn)


def _render_fts_text(experience_dict: dict[str, Any] | None, fallback_experience: str = "") -> str:
    data = experience_dict or {}
    values = {field: str(data.get(field) or "").strip() for field in EXPERIENCE_FIELDS}
    rendered = (
        f"问题: {values['problem']} 成败原因: {values['outcome_reason']} "
        f"有效做法: {values['action']} 禁忌: {values['anti_pattern']} "
        f"验证信号: {values['validation']} 适用条件: {values['scope']}"
    ).strip()
    if any(values.values()):
        return rendered
    return (fallback_experience or "").strip()


def _rebuild_fts(conn: sqlite3.Connection) -> None:
    rows = conn.execute("SELECT * FROM memories").fetchall()
    conn.execute("DELETE FROM memories_fts")
    for row in rows:
        row_dict = dict(row)
        experience_dict = {field: row_dict.get(field, "") for field in EXPERIENCE_FIELDS}
        conn.execute(
            """
            INSERT INTO memories_fts(rowid, task_text, experience, fail_reason, fts_text)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                int(row_dict["id"]),
                row_dict.get("task_text") or "",
                row_dict.get("experience") or "",
                row_dict.get("fail_reason") or "",
                _render_fts_text(experience_dict, row_dict.get("experience") or ""),
            ),
        )

def _pack_embedding(vec: Sequence[float]) -> bytes:
    values = list(vec)
    if len(values) != EMBEDDING_DIM:
        raise ValueError(f"embedding must be {EMBEDDING_DIM} dims, got {len(values)}")

    packed = array.array("f", (float(value) for value in values))
    if packed.itemsize != 4:
        raise RuntimeError("platform float array itemsize is not float32")
    return packed.tobytes()


def _unpack_embedding(blob: bytes | None) -> list[float]:
    if not blob:
        return []

    values = array.array("f")
    values.frombytes(blob)
    return list(values)


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0

    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for left, right in zip(a, b):
        dot += left * right
        norm_a += left * left
        norm_b += right * right

    if norm_a <= 0.0 or norm_b <= 0.0:
        return 0.0

    return dot / (math.sqrt(norm_a) * math.sqrt(norm_b))


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    data["embedding"] = _unpack_embedding(data["embedding"]) if data.get("embedding") else None

    tools_used = data.get("tools_used")
    if tools_used:
        try:
            data["tools_used"] = json.loads(tools_used)
        except json.JSONDecodeError:
            data["tools_used"] = []
    else:
        data["tools_used"] = []

    data["success"] = bool(data.get("success"))
    return data


def _empty_experience(root_cause: str = "unknown", stage: str = "run", confidence: float = 0.0) -> dict[str, Any]:
    data = {field: "" for field in EXPERIENCE_FIELDS}
    data.update({"root_cause": root_cause, "stage": stage, "confidence": float(confidence)})
    return data


def _normalize_experience_dict(value: dict[str, Any] | None) -> dict[str, Any]:
    data = _empty_experience()
    if isinstance(value, dict):
        for field in EXPERIENCE_FIELDS:
            data[field] = str(value.get(field) or "").strip()[:2000]
        root_cause = str(value.get("root_cause") or "unknown").strip()
        data["root_cause"] = root_cause if root_cause in ROOT_CAUSES else "unknown"
        stage = str(value.get("stage") or "run").strip()
        data["stage"] = stage if stage in STAGES else "run"
        try:
            confidence = float(value.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        data["confidence"] = max(0.0, min(1.0, confidence))
    return data


def _extract_json_object(text: str) -> dict[str, Any] | None:
    raw = (text or "").strip()
    if not raw:
        return None
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", raw, flags=re.S)
    if not match:
        return None
    try:
        value = json.loads(match.group(0))
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        return None


def _rule_root_cause(text: str) -> tuple[str, str, float] | None:
    haystack = (text or "").lower()
    if re.search(r"exit\s*code|timed?\s*out|timeout|超时", haystack):
        return "timeout", "run", 0.95
    if "404" in haystack or "not found" in haystack:
        return "tool_call", "tool", 0.9
    if "json decode" in haystack or "json.decoder" in haystack:
        return "llm_parse", "extract", 0.9
    if "ratelimit" in haystack or "rate limit" in haystack or "429" in haystack:
        return "resource", "run", 0.9
    if "key invalid" in haystack or "401" in haystack:
        return "dispatch_mismatch", "dispatch", 0.9
    if "connection" in haystack or "dns" in haystack:
        return "network", "tool", 0.85
    return None


def _llm_extract_helper(sub_hermes_output: str, task_text: str, ok: bool, fail_reason: str | None, tools_used: list | None) -> dict[str, Any] | None:
    prompt = (
        "你是蜂巢经验抽取器。只输出一个 JSON 对象，不要 Markdown。字段固定为 "
        "problem,outcome_reason,action,anti_pattern,validation,scope,root_cause,stage,confidence。\n"
        "中文填写前六段：问题、成败原因、有效做法、禁忌、验证信号、适用条件。失败允许空字符串。\n"
        "root_cause 只能是 data_source/tool_call/network/timeout/resource/llm_parse/impossible/dispatch_mismatch/unknown。\n"
        "stage 只能是 dispatch/run/tool/finalize/extract。confidence 为 0 到 1。\n\n"
        f"任务: {task_text[:3000]}\n成功: {bool(ok)}\n失败原因: {(fail_reason or '')[:1000]}\n"
        f"工具: {json.dumps(tools_used or [], ensure_ascii=False)}\n输出: {sub_hermes_output[:6000]}"
    )
    payload = json.dumps(
        {
            "model": EXTRACT_MODEL,
            "messages": [
                {"role": "system", "content": "你负责把执行结果压缩成可复用结构化经验。"},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.1,
            "max_tokens": 800,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    url = EXTRACT_BASE_URL.rstrip("/") + "/chat/completions"
    request = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {EXTRACT_API_KEY}"} if EXTRACT_API_KEY else {})}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=EXTRACT_TIMEOUT) as response:
            body = response.read().decode("utf-8")
        data = json.loads(body)
        content = data["choices"][0]["message"]["content"]
        parsed = _extract_json_object(content)
        return _normalize_experience_dict(parsed) if parsed else None
    except Exception as exc:
        sys.stderr.write(f"[hive] extract llm fail: {exc}\n")
        return None


def _clean_task_text(task_text: str) -> str:
    text = (task_text or "").strip()
    if not text:
        raise ValueError("task_text must not be empty")
    return text[:MAX_TASK_TEXT_LEN]


def _json_tools(tools_used: Sequence[str] | str | None) -> str | None:
    if tools_used is None:
        return None
    if isinstance(tools_used, str):
        try:
            parsed = json.loads(tools_used)
            if isinstance(parsed, list):
                return json.dumps(parsed, ensure_ascii=False)
        except json.JSONDecodeError:
            return json.dumps([tools_used], ensure_ascii=False)
        return json.dumps([tools_used], ensure_ascii=False)
    return json.dumps(list(tools_used), ensure_ascii=False)


def _quoted_fts_query(text: str) -> str:
    """FTS5 召回 query：拆 1-char 中文 + 英文/数字词。

    HIVE-PATCH-1.3.1: 改精确短语 -> 拆字符 OR 召回
    - unicode61 tokenizer 中文按字切，精确短语要求完全匹配
    - 改拆单字 + 英文词，FTS5 OR 召回更广
    - 召回广没关系，下游 embedding 重排 + decay_score 过滤
    """
    import re
    chars = re.findall(r'[\u4e00-\u9fff]', text or "")
    words = re.findall(r'[A-Za-z0-9]+', text or "")
    tokens = chars + [w for w in words if len(w) >= 2]
    return " ".join(tokens) if tokens else ""


def _normalize_task_type(task_type: str) -> str:
    value = (task_type or "general").strip().lower()
    if not value:
        return "general"
    return value[:64]


def _deterministic_embedding(text: str) -> list[float]:
    """
    本地兜底 embedding：4096 维、确定性、无网络依赖。

    如果配置了 OpenAI-compatible embedding 环境变量，会优先请求真实 embedding；
    否则使用 hash trick 生成可用于测试和冷启动的轻量向量。
    """
    vec = [0.0] * EMBEDDING_DIM
    tokens = text.lower().split()

    if not tokens:
        tokens = [text.lower()]

    for token in tokens:
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=16).digest()
        for offset in range(0, len(digest), 4):
            raw = int.from_bytes(digest[offset : offset + 4], "little", signed=False)
            index = raw % EMBEDDING_DIM
            sign = 1.0 if raw & 1 else -1.0
            vec[index] += sign

    norm = math.sqrt(sum(value * value for value in vec))
    if norm <= 0.0:
        return vec

    return [value / norm for value in vec]


def get_embedding(text: str) -> list[float] | None:
    """
    从 hive_pheromones 逻辑抽出的独立 embedding 入口。

    支持 OpenAI-compatible 环境变量：
    - HIVE_EMBEDDING_BASE_URL / OPENAI_BASE_URL，默认 https://api.openai.com/v1
    - HIVE_EMBEDDING_API_KEY / OPENAI_API_KEY
    - HIVE_EMBEDDING_MODEL，默认 text-embedding-3-large

    若未配置 API key 或远端失败，返回本地确定性 4096 维 embedding，保证 main() 可运行。
    """
    clean_text = (text or "").strip()
    if not clean_text:
        return None

    api_key = os.getenv("HIVE_EMBEDDING_API_KEY") or os.getenv("OPENAI_API_KEY")
    base_url = (os.getenv("HIVE_EMBEDDING_BASE_URL") or os.getenv("OPENAI_BASE_URL") or "").rstrip("/")
    model = os.getenv("HIVE_EMBEDDING_MODEL", "text-embedding-3-large")

    if api_key and base_url:
        url = f"{base_url}/embeddings"
        payload = json.dumps({"model": model, "input": clean_text}).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        # HIVE-PATCH-1.3.1: embedding 加重试 2 次 + 短超时 5s
        last_err = None
        for attempt in range(3):  # 1 次原始 + 2 次重试
            try:
                with urllib.request.urlopen(request, timeout=5) as response:
                    body = response.read().decode("utf-8")
                data = json.loads(body)
                embedding = data["data"][0]["embedding"]
                if isinstance(embedding, list):
                    if len(embedding) == EMBEDDING_DIM:
                        return [float(value) for value in embedding]
                    if len(embedding) > EMBEDDING_DIM:
                        return [float(value) for value in embedding[:EMBEDDING_DIM]]
                    padded = [float(value) for value in embedding]
                    padded.extend([0.0] * (EMBEDDING_DIM - len(padded)))
                    return padded
            except (KeyError, json.JSONDecodeError, TimeoutError, urllib.error.URLError, OSError) as e:
                last_err = e
                if attempt < 2:
                    time.sleep(1.0 * (attempt + 1))  # 1s, 2s backoff
                continue
        # 3 次全失败：fallback 到 deterministic
        if last_err:
            sys.stderr.write("[hive] embedding 3 次失败, fallback: %s\n" % last_err)
    return _deterministic_embedding(clean_text)


# HIVE-PATCH-1.4: API add
def add(
    task_type: str,
    task_text: str,
    huluwa_id: int,
    experience: str,
    tools_used: Sequence[str] | str | None,
    duration_ms: int | None,
    success: bool | int,
    fail_reason: str | None,
    embedding: Sequence[float] | None = None,
    experience_dict: dict[str, Any] | None = None,
) -> int:
    init_db()
    clean_text = _clean_task_text(task_text)
    clean_task_type = _normalize_task_type(task_type)
    clean_experience = (experience or "").strip()
    clean_fail_reason = (fail_reason or "").strip() or None
    structured = _normalize_experience_dict(experience_dict)
    fts_text = _render_fts_text(structured if experience_dict else None, clean_experience)

    if not clean_experience:
        raise ValueError("experience must not be empty")

    if embedding is None:
        embedding = get_embedding(clean_text)

    embedding_blob = _pack_embedding(embedding) if embedding is not None else None
    created_at = _now()
    expires_at = created_at + EXPIRE_DAYS * SECONDS_PER_DAY

    with _connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO memories (
                task_type, task_text, huluwa_id, embedding, experience,
                tools_used, duration_ms, success, fail_reason,
                use_count, last_used_at, decay_score, created_at, expires_at,
                problem, outcome_reason, action, anti_pattern, validation, scope,
                root_cause, stage, confidence
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, 1.0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                clean_task_type,
                clean_text,
                int(huluwa_id),
                embedding_blob,
                clean_experience,
                _json_tools(tools_used),
                duration_ms,
                1 if bool(success) else 0,
                clean_fail_reason,
                created_at,
                expires_at,
                structured["problem"],
                structured["outcome_reason"],
                structured["action"],
                structured["anti_pattern"],
                structured["validation"],
                structured["scope"],
                structured["root_cause"],
                structured["stage"],
                structured["confidence"],
            ),
        )
        mem_id = int(cursor.lastrowid)
        conn.execute(
            """
            INSERT INTO memories_fts(rowid, task_text, experience, fail_reason, fts_text)
            VALUES (?, ?, ?, ?, ?)
            """,
            (mem_id, clean_text, clean_experience, clean_fail_reason or "", fts_text),
        )

    decay()
    return mem_id

def _fetch_rows_by_ids(conn: sqlite3.Connection, ids: Iterable[int]) -> dict[int, dict[str, Any]]:
    mem_ids = list(dict.fromkeys(int(mem_id) for mem_id in ids))
    if not mem_ids:
        return {}

    placeholders = ",".join("?" for _ in mem_ids)
    rows = conn.execute(
        f"SELECT * FROM memories WHERE id IN ({placeholders})",
        mem_ids,
    ).fetchall()
    return {int(row["id"]): _row_to_dict(row) for row in rows}


def _fts_recall(conn: sqlite3.Connection, task_text: str, task_type: str | None) -> dict[int, float]:
    params: list[Any] = [_quoted_fts_query(task_text)]
    type_filter = ""

    if task_type:
        type_filter = "AND m.task_type = ?"
        params.append(_normalize_task_type(task_type))

    rows = conn.execute(
        f"""
        SELECT m.id, bm25(memories_fts) AS rank
        FROM memories_fts
        JOIN memories m ON m.id = memories_fts.rowid
        WHERE memories_fts MATCH ?
        {type_filter}
        ORDER BY rank
        LIMIT 25
        """,
        params,
    ).fetchall()

    if not rows:
        return {}

    scores: dict[int, float] = {}
    total = len(rows)
    for index, row in enumerate(rows):
        scores[int(row["id"])] = 1.0 - (index / max(total, 1))
    return scores


def _embedding_recall(
    conn: sqlite3.Connection,
    query_embedding: Sequence[float] | None,
    task_type: str | None,
) -> dict[int, float]:
    if query_embedding is None:
        return {}

    params: list[Any] = []
    type_filter = ""

    if task_type:
        type_filter = "AND task_type = ?"
        params.append(_normalize_task_type(task_type))

    rows = conn.execute(
        f"""
        SELECT id, embedding
        FROM memories
        WHERE embedding IS NOT NULL
        {type_filter}
        """,
        params,
    ).fetchall()

    scored: list[tuple[int, float]] = []
    for row in rows:
        candidate_embedding = _unpack_embedding(row["embedding"])
        scored.append((int(row["id"]), _cosine(query_embedding, candidate_embedding)))

    scored.sort(key=lambda item: item[1], reverse=True)
    return dict(scored[:25])


# HIVE-PATCH-1.3: API query
def query(task_text: str, task_type: str | None = None, top_k: int = 5) -> list[dict[str, Any]]:
    init_db()  # HIVE-PATCH-1.3: 防御性
    clean_text = _clean_task_text(task_text)
    clean_top_k = max(1, int(top_k))
    query_embedding = get_embedding(clean_text)

    with _connect() as conn:
        fts_scores = _fts_recall(conn, clean_text, task_type)
        cosine_scores = _embedding_recall(conn, query_embedding, task_type)
        candidate_ids = set(fts_scores) | set(cosine_scores)

        rows_by_id = _fetch_rows_by_ids(conn, candidate_ids)

    results: list[dict[str, Any]] = []
    for mem_id, memory in rows_by_id.items():
        cosine_score = cosine_scores.get(mem_id, 0.0)
        fts_norm = fts_scores.get(mem_id, 0.0)
        decay_score = float(memory.get("decay_score") or 0.0)
        confidence = float(memory.get("confidence") or 0.0)
        confidence_factor = 0.70 + 0.30 * confidence
        rerank_score = (0.55 * cosine_score + 0.30 * fts_norm + 0.15 * decay_score) * confidence_factor

        memory["cosine_score"] = cosine_score
        memory["fts_norm"] = fts_norm
        memory["rerank_score"] = rerank_score
        results.append(memory)

    results.sort(key=lambda item: item["rerank_score"], reverse=True)
    return results[:clean_top_k]


# HIVE-PATCH-1.4: API update_memory
def update_memory(
    mem_id: int,
    experience: str | None = None,
    success: bool | int | None = None,
    fail_reason: str | None = None,
    duration_ms: int | None = None,
    tools_used: Sequence[str] | str | None = None,
    experience_dict: dict[str, Any] | None = None,
) -> None:
    """派单时 add 占位，跑完 update，并同步维护结构化经验与 FTS。"""
    sets = []
    params = []
    clean_experience = None
    clean_fail_reason = None
    structured = _normalize_experience_dict(experience_dict) if experience_dict is not None else None

    if experience is not None:
        clean_experience = (experience or "").strip()
        sets.append("experience = ?")
        params.append(clean_experience)
    if success is not None:
        sets.append("success = ?")
        params.append(1 if bool(success) else 0)
    if fail_reason is not None:
        clean_fail_reason = (fail_reason or "").strip() or None
        sets.append("fail_reason = ?")
        params.append(clean_fail_reason)
    if duration_ms is not None:
        sets.append("duration_ms = ?")
        params.append(int(duration_ms))
    if tools_used is not None:
        sets.append("tools_used = ?")
        params.append(_json_tools(tools_used))
    if structured is not None:
        for field in EXPERIENCE_FIELDS:
            sets.append(f"{field} = ?")
            params.append(structured[field])
        for field in ATTRIBUTION_FIELDS:
            sets.append(f"{field} = ?")
            params.append(structured[field])
    if not sets:
        return

    with _connect() as conn:
        params_with_id = [*params, int(mem_id)]
        conn.execute(f"UPDATE memories SET {', '.join(sets)} WHERE id = ?", params_with_id)
        row = conn.execute("SELECT * FROM memories WHERE id = ?", (int(mem_id),)).fetchone()
        if row:
            row_dict = dict(row)
            fts_structured = {field: row_dict.get(field, "") for field in EXPERIENCE_FIELDS}
            fts_text = _render_fts_text(fts_structured, row_dict.get("experience") or "")
            conn.execute("DELETE FROM memories_fts WHERE rowid = ?", (int(mem_id),))
            conn.execute(
                """
                INSERT INTO memories_fts(rowid, task_text, experience, fail_reason, fts_text)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    int(mem_id),
                    row_dict.get("task_text") or "",
                    row_dict.get("experience") or "",
                    row_dict.get("fail_reason") or "",
                    fts_text,
                ),
            )



# HIVE-PATCH-1.3: API touch
def touch(mem_id: int) -> None:
    with _connect() as conn:
        conn.execute(
            """
            UPDATE memories
            SET use_count = COALESCE(use_count, 0) + 1,
                last_used_at = ?
            WHERE id = ?
            """,
            (_now(), int(mem_id)),
        )


# HIVE-PATCH-1.3: API decay
def decay() -> None:
    now = _now()

    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, created_at, expires_at, success, use_count
            FROM memories
            """
        ).fetchall()

        delete_ids: list[int] = []
        updates: list[tuple[float, int]] = []

        for row in rows:
            mem_id = int(row["id"])
            created_at = float(row["created_at"])
            expires_at = float(row["expires_at"])
            success = int(row["success"])
            use_count = int(row["use_count"] or 0)

            age_days = max(0.0, (now - created_at) / SECONDS_PER_DAY)
            success_boost = 1.2 if success == 1 else 0.7
            usage_boost = 1.0 + math.log1p(use_count) / 5.0
            decay_score = 1.0 * math.exp(-age_days / 30.0) * success_boost * usage_boost

            if expires_at < now:
                delete_ids.append(mem_id)
                continue

            if decay_score < 0.15 and use_count < 2 and age_days > 7.0:
                delete_ids.append(mem_id)
                continue

            updates.append((decay_score, mem_id))

        conn.executemany(
            "UPDATE memories SET decay_score = ? WHERE id = ?",
            updates,
        )

        for mem_id in delete_ids:
            conn.execute("DELETE FROM memories_fts WHERE rowid = ?", (mem_id,))
            conn.execute("DELETE FROM memories WHERE id = ?", (mem_id,))


def format_injection(memories: list[dict[str, Any]]) -> str:
    lines = [f"[蜂巢群体经验 - top {len(memories)}]"]

    for index, memory in enumerate(memories, start=1):
        task_type = memory.get("task_type", "general")
        task_text = str(memory.get("task_text", "")).strip()
        title = task_text[:80] if task_text else "无标题经验"
        success = "成功" if memory.get("success") else "失败"
        fail_reason = str(memory.get("fail_reason") or "").strip()
        root_cause = str(memory.get("root_cause") or "unknown").strip()
        confidence = float(memory.get("confidence") or 0.0)
        use_count = int(memory.get("use_count") or 0)

        lines.append(f"{index}. [{task_type}] {title}")
        lines.append(f"   - 问题: {str(memory.get('problem') or '').strip()}")
        lines.append(f"   - 成败原因: {str(memory.get('outcome_reason') or '').strip()}")
        lines.append(f"   - 有效做法: {str(memory.get('action') or '').strip()}")
        lines.append(f"   - 禁忌: {str(memory.get('anti_pattern') or '').strip()}")
        lines.append(f"   - 验证信号: {str(memory.get('validation') or success).strip()}")
        lines.append(f"   - 适用条件: {str(memory.get('scope') or '').strip()}")
        if fail_reason:
            lines.append(f"   - 失败原因: {fail_reason}")
        lines.append(f"   - 归因: {root_cause} / 置信度 {confidence:.2f} / 引用 {use_count}")

    return "\n".join(lines)


def extract_experience(
    sub_hermes_output: str,
    task_text: str,
    ok: bool,
    fail_reason: str | None,
    tools_used: list | None,
) -> dict[str, Any]:
    combined = "\n".join(str(part or "") for part in [sub_hermes_output, task_text, fail_reason, json.dumps(tools_used or [], ensure_ascii=False)])
    rule = None if ok else _rule_root_cause(combined)
    llm_data = _llm_extract_helper(sub_hermes_output, task_text, ok, fail_reason, tools_used)
    data = _normalize_experience_dict(llm_data)

    if rule:
        data["root_cause"], data["stage"], data["confidence"] = rule
    elif not data.get("root_cause") or data["root_cause"] == "unknown":
        data["root_cause"] = "unknown"
        data["stage"] = data.get("stage") if data.get("stage") in STAGES else "run"
        data["confidence"] = float(data.get("confidence") or 0.0)
    if ok and data["root_cause"] == "unknown":
        data["confidence"] = max(float(data.get("confidence") or 0.0), 0.5 if any(data.get(f) for f in EXPERIENCE_FIELDS) else 0.0)
    return _normalize_experience_dict(data)

def _print_memory(prefix: str, memory: dict[str, Any]) -> None:
    compact = {
        "id": memory.get("id"),
        "task_type": memory.get("task_type"),
        "task_text": memory.get("task_text"),
        "success": memory.get("success"),
        "use_count": memory.get("use_count"),
        "decay_score": round(float(memory.get("decay_score") or 0.0), 6),
        "cosine_score": round(float(memory.get("cosine_score") or 0.0), 6),
        "fts_norm": round(float(memory.get("fts_norm") or 0.0), 6),
        "rerank_score": round(float(memory.get("rerank_score") or 0.0), 6),
        "experience": memory.get("experience"),
        "fail_reason": memory.get("fail_reason"),
    }
    print(prefix, json.dumps(compact, ensure_ascii=False, indent=2))


def main() -> None:
    init_db()

    samples = [
        {
            "task_type": "finance_realtime",
            "task_text": "查询英伟达 NVDA 实时财报与盘前价格，生成交易风险摘要",
            "huluwa_id": 1,
            "experience": "实时金融任务优先使用权威行情源交叉验证，价格、时间戳、币种必须一起记录。",
            "tools_used": ["web", "market_data"],
            "duration_ms": 1280,
            "success": 1,
            "fail_reason": None,
        },
        {
            "task_type": "finance_realtime",
            "task_text": "查询小盘股实时盘口并判断是否适合追涨",
            "huluwa_id": 2,
            "experience": "低流动性标的容易出现延迟报价，缺少成交量验证时不要给确定性结论。",
            "tools_used": ["web"],
            "duration_ms": 2210,
            "success": 0,
            "fail_reason": "行情源时间戳滞后，盘口数据无法互证。",
        },
        {
            "task_type": "code",
            "task_text": "修复 Python SQLite FTS5 查询和 embedding 混合召回排序",
            "huluwa_id": 3,
            "experience": "FTS5 负责关键词召回，embedding 负责语义召回，最终必须合并去重后统一重排。",
            "tools_used": ["terminal", "sqlite3"],
            "duration_ms": 980,
            "success": 1,
            "fail_reason": None,
        },
    ]

    mem_ids = [add(**sample) for sample in samples]
    print("inserted:", mem_ids)

    results = query("实时金融行情 查询 风险 验证", task_type="finance_realtime", top_k=5)
    print("query_results:")
    for result in results:
        _print_memory("-", result)

    if results:
        touch(int(results[0]["id"]))
        print("touched:", results[0]["id"])

    decay()
    print("decay_done")

    after_touch = query("实时金融行情 查询 风险 验证", task_type="finance_realtime", top_k=5)
    print("after_touch_and_decay:")
    for result in after_touch:
        _print_memory("-", result)

    print(format_injection(after_touch))


if __name__ == "__main__":
    main()