#!/usr/bin/env python3
"""
hive_pheromones.py — 蜂巢气味/信息素池

兼容 1.0~1.4 API，并新增 1.7 task_type 独立信息素：
- huluwa_task_pheromones(huluwa_id, task_type, score, success_count, fail_count, last_updated)
- update/get/list task_type pheromone
- pick_by_pheromone_v17: 0.7 * scent similarity + 0.3 * task_type_score
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

import array
import hashlib
import json
import math
import os
import sqlite3
import time
import urllib.error
import urllib.request
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

HIVE_DIR = Path(os.environ.get("HERMES_HOME", "~/.hermes")) / ".hermes/hive"
HIVE_DB = HIVE_DIR / "hive.db"
EMBEDDING_DIM = 4096
WINNER_LIMIT = 3

HULUWA_SCENTS = {
    # === 1~8 娃：agnes-2.0-flash 集群 (快/量大/简单) ===
    1: ("大娃", "跑批执行 文案生成 电商内容 批量处理 运营执行"),
    2: ("二娃", "代码编写 bug 修复 技术实现 Python SQLite 工程实现"),
    3: ("三娃", "数据采集 URL抓取 邮箱提取 公开信息搜集 爬虫"),
    4: ("四娃", "翻译 多语言 本地化 跨语言处理 英文润色"),
    5: ("五娃", "数据分析 统计 报表生成 趋势识别 财经分析"),
    6: ("六娃", "对话 客服 文本润色 沟通类任务 表达优化"),
    7: ("七娃", "搜索 研究 信息聚合 情报收集 实时信息 金融行情"),
    8: ("八娃", "通用任务 后备 兜底 所有场景 综合处理"),
    # === 9~13 娃：gpt-5.5 集群 (慢/深度/复杂) — llm 中转 ===
    9:  ("九娃",   "gpt-5.5 通用推理 兜底 综合任务 复杂问题"),
    10: ("十娃",   "gpt-5.5 财经分析 股票 投资 经济 长文推理"),
    11: ("十一娃", "gpt-5.5 代码生成 bug 修复 工程实现 复杂技术"),
    12: ("十二娃", "gpt-5.5 长文写作 文案 翻译 多语言 表达优化"),
    13: ("十三娃", "gpt-5.5 实时信息 搜索 研究 情报 快速分析"),
}

last_n_assignments: dict[str, deque[int]] = defaultdict(lambda: deque(maxlen=WINNER_LIMIT))


def _now() -> float:
    return time.time()


def _connect() -> sqlite3.Connection:
    HIVE_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(HIVE_DB), timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def _clamp_score(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _normalize_task_type(task_type: str | None) -> str:
    value = (task_type or "general").strip().lower()
    return (value or "general")[:64]


def init_db() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pheromones (
                huluwa_id INTEGER PRIMARY KEY,
                name TEXT,
                scent_text TEXT,
                total_tasks INTEGER DEFAULT 0,
                total_ok INTEGER DEFAULT 0,
                total_fail INTEGER DEFAULT 0,
                success_rate REAL DEFAULT 1.0,
                avg_duration_ms INTEGER DEFAULT 0,
                current_load INTEGER DEFAULT 0,
                last_active_at TEXT,
                decay_score REAL DEFAULT 1.0
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS task_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                huluwa_id INTEGER,
                task_id TEXT,
                task_text TEXT,
                task_embedding BLOB,
                ok INTEGER,
                duration_ms INTEGER,
                dispatched_at TEXT,
                FOREIGN KEY (huluwa_id) REFERENCES pheromones(huluwa_id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS hive_dispatches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT,
                task_text TEXT,
                huluwa_id INTEGER,
                match_method TEXT,
                match_score REAL,
                ok INTEGER,
                duration_ms INTEGER,
                dispatched_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS scent_text_cache (
                huluwa_id INTEGER PRIMARY KEY,
                name TEXT,
                scent_text TEXT,
                updated_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS scent_embedding_cache (
                huluwa_id INTEGER NOT NULL,
                scent_hash TEXT NOT NULL,
                embedding BLOB NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY (huluwa_id, scent_hash)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS huluwa_task_pheromones (
                huluwa_id INTEGER NOT NULL,
                task_type TEXT NOT NULL,
                score REAL NOT NULL DEFAULT 0.5,
                success_count INTEGER NOT NULL DEFAULT 0,
                fail_count INTEGER NOT NULL DEFAULT 0,
                last_updated REAL NOT NULL,
                PRIMARY KEY (huluwa_id, task_type)
            )
            """
        )
        now_iso = datetime.utcnow().isoformat(timespec="seconds")
        for huluwa_id, (name, scent_text) in HULUWA_SCENTS.items():
            conn.execute(
                """
                INSERT INTO pheromones (huluwa_id, name, scent_text, last_active_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(huluwa_id) DO UPDATE SET
                    name = COALESCE(pheromones.name, excluded.name),
                    scent_text = COALESCE(pheromones.scent_text, excluded.scent_text)
                """,
                (huluwa_id, name, scent_text, now_iso),
            )
            conn.execute(
                """
                INSERT INTO scent_text_cache (huluwa_id, name, scent_text, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(huluwa_id) DO NOTHING
                """,
                (huluwa_id, name, scent_text, now_iso),
            )


def scent_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _pack_embedding(vec: Sequence[float]) -> bytes:
    packed = array.array("f", (float(value) for value in vec))
    return packed.tobytes()


def _unpack_embedding(blob: bytes | None) -> list[float] | None:
    if not blob:
        return None
    values = array.array("f")
    values.frombytes(blob)
    return list(values)


def _deterministic_embedding(text: str, dim: int = EMBEDDING_DIM) -> list[float]:
    vec = [0.0] * dim
    tokens = (text or "").lower().split() or [(text or "").lower()]
    for token in tokens:
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=16).digest()
        for offset in range(0, len(digest), 4):
            raw = int.from_bytes(digest[offset : offset + 4], "little", signed=False)
            vec[raw % dim] += 1.0 if raw & 1 else -1.0
    norm = math.sqrt(sum(value * value for value in vec))
    return [value / norm for value in vec] if norm > 0 else vec


def get_embedding(text: str) -> list[float] | None:
    clean_text = (text or "").strip()
    if not clean_text:
        return None

    api_key = os.getenv("SILICONFLOW_API_KEY") or os.getenv("HIVE_EMBEDDING_API_KEY") or os.getenv("OPENAI_API_KEY")
    if os.getenv("SILICONFLOW_API_KEY"):
        url = "https://api.siliconflow.cn/v1/embeddings"
        model = os.getenv("HIVE_EMBEDDING_MODEL", "Qwen/Qwen3-Embedding-8B")
    else:
        base_url = (os.getenv("HIVE_EMBEDDING_BASE_URL") or os.getenv("OPENAI_BASE_URL") or "").rstrip("/")
        url = f"{base_url}/embeddings" if base_url else ""
        model = os.getenv("HIVE_EMBEDDING_MODEL", "text-embedding-3-large")

    if api_key and url:
        payload = json.dumps({"model": model, "input": clean_text[:2000], "encoding_format": "float"}).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=payload,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=8) as response:
                data = json.loads(response.read().decode("utf-8"))
            embedding = data["data"][0]["embedding"]
            if isinstance(embedding, list):
                return [float(value) for value in embedding]
        except (KeyError, json.JSONDecodeError, TimeoutError, urllib.error.URLError, OSError):
            pass
    return _deterministic_embedding(clean_text)


def cosine(a: Sequence[float] | None, b: Sequence[float] | None) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(left * right for left, right in zip(a, b))
    norm_a = math.sqrt(sum(value * value for value in a))
    norm_b = math.sqrt(sum(value * value for value in b))
    if norm_a <= 0.0 or norm_b <= 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def get_cached_scent_embedding(huluwa_id: int, scent_hash_value: str) -> list[float] | None:
    init_db()
    with _connect() as conn:
        row = conn.execute(
            "SELECT embedding FROM scent_embedding_cache WHERE huluwa_id = ? AND scent_hash = ?",
            (int(huluwa_id), scent_hash_value),
        ).fetchone()
    return _unpack_embedding(row["embedding"]) if row else None


def set_cached_scent_embedding(huluwa_id: int, embedding: Sequence[float] | None, scent_hash_value: str) -> None:
    if embedding is None:
        return
    init_db()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO scent_embedding_cache (huluwa_id, scent_hash, embedding, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(huluwa_id, scent_hash) DO UPDATE SET
                embedding = excluded.embedding,
                updated_at = excluded.updated_at
            """,
            (int(huluwa_id), scent_hash_value, _pack_embedding(embedding), _now()),
        )


def list_pheromones() -> list[dict[str, Any]]:
    init_db()
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM pheromones ORDER BY huluwa_id").fetchall()
    return [dict(row) for row in rows]


def record_task(
    huluwa_id: int,
    task_id: str,
    task_text: str,
    ok: bool,
    duration_ms: int | None,
    match_method: str = "unknown",
    match_score: float = 0.0,
) -> None:
    init_db()
    now_iso = datetime.utcnow().isoformat(timespec="seconds")
    duration = int(duration_ms or 0)
    with _connect() as conn:
        row = conn.execute(
            "SELECT total_tasks, total_ok, total_fail, avg_duration_ms FROM pheromones WHERE huluwa_id = ?",
            (int(huluwa_id),),
        ).fetchone()
        total_tasks = int(row["total_tasks"] or 0) + 1 if row else 1
        total_ok = int(row["total_ok"] or 0) + (1 if ok else 0) if row else (1 if ok else 0)
        total_fail = int(row["total_fail"] or 0) + (0 if ok else 1) if row else (0 if ok else 1)
        old_avg = int(row["avg_duration_ms"] or 0) if row else 0
        avg_duration = int(((old_avg * (total_tasks - 1)) + duration) / total_tasks) if total_tasks else duration
        success_rate = total_ok / total_tasks if total_tasks else 1.0

        conn.execute(
            """
            UPDATE pheromones
            SET total_tasks = ?, total_ok = ?, total_fail = ?, success_rate = ?,
                avg_duration_ms = ?, current_load = MAX(0, COALESCE(current_load, 0) - 1),
                last_active_at = ?, decay_score = 1.0
            WHERE huluwa_id = ?
            """,
            (total_tasks, total_ok, total_fail, success_rate, avg_duration, now_iso, int(huluwa_id)),
        )
        conn.execute(
            """
            INSERT INTO task_history (huluwa_id, task_id, task_text, ok, duration_ms, dispatched_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (int(huluwa_id), str(task_id), task_text, 1 if ok else 0, duration, now_iso),
        )
        conn.execute(
            """
            INSERT INTO hive_dispatches (task_id, task_text, huluwa_id, match_method, match_score, ok, duration_ms, dispatched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (str(task_id), task_text, int(huluwa_id), match_method, float(match_score), 1 if ok else 0, duration, now_iso),
        )


def increment_load(huluwa_id: int) -> None:
    init_db()
    with _connect() as conn:
        conn.execute(
            "UPDATE pheromones SET current_load = COALESCE(current_load, 0) + 1 WHERE huluwa_id = ?",
            (int(huluwa_id),),
        )


def decay() -> None:
    init_db()
    with _connect() as conn:
        conn.execute(
            """
            UPDATE pheromones
            SET decay_score = MAX(0.1, COALESCE(decay_score, 1.0) * 0.95),
                current_load = MAX(0, COALESCE(current_load, 0) - 1)
            """
        )


def update_huluwa_task_pheromone(huluwa_id: int, task_type: str, ok: bool, root_cause: str | None = None) -> None:
    init_db()
    clean_task_type = _normalize_task_type(task_type)
    clean_root_cause = (root_cause or "unknown").strip().lower()
    delta = 0.05 if ok else (-0.1 if clean_root_cause != "unknown" else 0.0)
    success_inc = 1 if ok else 0
    fail_inc = 0 if ok else 1
    now = _now()

    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO huluwa_task_pheromones (
                huluwa_id, task_type, score, success_count, fail_count, last_updated
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(huluwa_id, task_type) DO UPDATE SET
                score = MIN(1.0, MAX(0.0, huluwa_task_pheromones.score + excluded.score - 0.5)),
                success_count = huluwa_task_pheromones.success_count + excluded.success_count,
                fail_count = huluwa_task_pheromones.fail_count + excluded.fail_count,
                last_updated = excluded.last_updated
            """,
            (int(huluwa_id), clean_task_type, _clamp_score(0.5 + delta), success_inc, fail_inc, now),
        )
    try:
        from hive_meta_cognition import reflect_on_action
        reflect_on_action(
            "pheromone_update",
            {"huluwa_id": int(huluwa_id), "task_type": clean_task_type, "ok": bool(ok), "delta": delta},
            "任务结果触发 task_type 信息素调权",
            "hive_pheromones.update_huluwa_task_pheromone",
        )
    except Exception:
        pass


def get_huluwa_task_pheromone(huluwa_id: int, task_type: str) -> dict[str, Any] | None:
    init_db()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM huluwa_task_pheromones WHERE huluwa_id = ? AND task_type = ?",
            (int(huluwa_id), _normalize_task_type(task_type)),
        ).fetchone()
    return _row_to_dict(row)


def list_huluwa_task_pheromones() -> list[dict[str, Any]]:
    init_db()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM huluwa_task_pheromones ORDER BY task_type, huluwa_id"
        ).fetchall()
    return [dict(row) for row in rows]


def _scent_embeddings() -> dict[int, dict[str, Any]]:
    rows = list_pheromones()
    embeddings: dict[int, dict[str, Any]] = {}
    for row in rows:
        scent_text = row.get("scent_text") or row.get("name") or ""
        scent_hash_value = scent_hash(scent_text)
        embedding = get_cached_scent_embedding(int(row["huluwa_id"]), scent_hash_value)
        if embedding is None:
            embedding = get_embedding(scent_text)
            set_cached_scent_embedding(int(row["huluwa_id"]), embedding, scent_hash_value)
        embeddings[int(row["huluwa_id"])] = {
            "name": row.get("name"),
            "scent_text": scent_text,
            "embedding": embedding,
        }
    return embeddings


def pick_by_pheromone(task_text: str, exclude: set[int] | None = None) -> tuple[int | None, float, str]:
    exclude = exclude or set()
    task_embedding = get_embedding(task_text)
    if not task_embedding:
        return None, 0.0, "no_embedding"

    scores: list[tuple[int, float]] = []
    for huluwa_id, info in _scent_embeddings().items():
        if huluwa_id in exclude:
            continue
        score = cosine(task_embedding, info.get("embedding"))
        scores.append((huluwa_id, score))

    if not scores:
        return None, 0.0, "no_scent"
    scores.sort(key=lambda item: item[1], reverse=True)
    top_id, top_score = scores[0]
    top2_score = scores[1][1] if len(scores) > 1 else 0.0
    if top_score < 0.3:
        return None, top_score, "low_confidence"
    if top_score - top2_score < 0.05:
        return None, top_score, "low_margin"
    return top_id, top_score, "pheromone"


def pick_by_pheromone_v17(
    task_text: str,
    task_type: str,
    exclude: set[int] | None = None,
) -> tuple[int | None, float, str]:
    exclude = exclude or set()
    clean_task_type = _normalize_task_type(task_type)
    task_embedding = get_embedding(task_text)
    if not task_embedding:
        return None, 0.0, "no_embedding_v17"

    pheromone_rows = {
        int(row["huluwa_id"]): row
        for row in list_huluwa_task_pheromones()
        if row["task_type"] == clean_task_type
    }
    has_task_type_data = bool(pheromone_rows)

    scores: list[tuple[int, float, float, float]] = []
    for huluwa_id, info in _scent_embeddings().items():
        if huluwa_id in exclude:
            continue
        similarity = cosine(task_embedding, info.get("embedding"))
        task_type_score = float(pheromone_rows.get(huluwa_id, {}).get("score", 0.5))
        combined = (0.7 * similarity + 0.3 * task_type_score) if has_task_type_data else similarity
        scores.append((huluwa_id, combined, similarity, task_type_score))

    if not scores:
        return None, 0.0, "no_scent_v17"

    scores.sort(key=lambda item: item[1], reverse=True)
    chosen_id, chosen_score, _, _ = scores[0]
    method = "pheromone_v17" if has_task_type_data else "pheromone_v17_similarity"

    recent = last_n_assignments[clean_task_type]
    if len(recent) == WINNER_LIMIT and all(huluwa_id == chosen_id for huluwa_id in recent):
        if len(scores) > 1:
            chosen_id, chosen_score, _, _ = scores[1]
            method += ":anti_winner_take_all"

    recent.append(chosen_id)
    return chosen_id, chosen_score, method


def main() -> None:
    init_db()
    print(json.dumps({"pheromones": list_pheromones(), "task_pheromones": list_huluwa_task_pheromones()}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
