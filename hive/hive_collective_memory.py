#!/usr/bin/env python3
"""
hive_collective_memory — 蜂巢集体记忆

把娃完成任务后的可复用经验写入 hive.db，让后续派单前可以主动召回。
"""

from __future__ import annotations

import math
import os
import re
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Sequence

_HERMES_HOME = os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")
_HIVE_DIR = os.path.join(_HERMES_HOME, "hive")
if _HIVE_DIR not in sys.path:
    sys.path.insert(0, _HIVE_DIR)
if _HERMES_HOME not in sys.path:
    sys.path.insert(0, _HERMES_HOME)

HIVE_DIR = Path(os.environ.get("HERMES_HOME", "~/.hermes")) / ".hermes/hive"
HIVE_DB = HIVE_DIR / "hive.db"
SECONDS_PER_DAY = 86400.0


def _now() -> float:
    return time.time()


def _connect() -> sqlite3.Connection:
    HIVE_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(HIVE_DB), timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _clamp_score(value: float) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.5


def _normalize_task_type(task_type: str | None) -> str:
    value = (task_type or "general").strip().lower()
    return (value or "general")[:64]


def init_db() -> None:
    """初始化集体记忆表。"""
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS collective_lessons (
              lesson_id INTEGER PRIMARY KEY AUTOINCREMENT,
              worker_id INTEGER NOT NULL,
              task_type TEXT NOT NULL,
              task_excerpt TEXT,
              approach TEXT NOT NULL,
              reusable_pattern TEXT NOT NULL,
              pitfalls TEXT,
              evidence TEXT,
              use_count INTEGER DEFAULT 0,
              success_count INTEGER DEFAULT 0,
              fail_count INTEGER DEFAULT 0,
              quality_score REAL DEFAULT 0.5,
              promoted_to_skill TEXT,
              created_at REAL NOT NULL,
              last_used_at REAL
            );
            CREATE INDEX IF NOT EXISTS idx_lessons_task_type ON collective_lessons(task_type);
            CREATE INDEX IF NOT EXISTS idx_lessons_worker ON collective_lessons(worker_id);
            CREATE INDEX IF NOT EXISTS idx_lessons_quality ON collective_lessons(quality_score DESC);
            """
        )


def _keywords(text: str) -> set[str]:
    return set(re.findall(r"[\w\u4e00-\u9fff]{2,}", (text or "").lower()))


def _keyword_similarity(task_text: str, lesson: dict[str, Any]) -> float:
    query_words = _keywords(task_text)
    lesson_words = _keywords(" ".join(str(lesson.get(key) or "") for key in (
        "task_excerpt", "approach", "reusable_pattern", "pitfalls", "evidence"
    )))
    if not query_words or not lesson_words:
        return 0.0
    return len(query_words & lesson_words) / max(1, min(len(query_words), len(lesson_words)))


def _cosine(left: Sequence[float] | None, right: Sequence[float] | None) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    norm_left = math.sqrt(sum(a * a for a in left))
    norm_right = math.sqrt(sum(b * b for b in right))
    if norm_left <= 0 or norm_right <= 0:
        return 0.0
    return dot / (norm_left * norm_right)


def _encode(text: str) -> list[float] | None:
    try:
        import hive_kb as hk
        if hasattr(hk, "encode"):
            return hk.encode(text)  # type: ignore[attr-defined]
        if hasattr(hk, "get_embedding"):
            return hk.get_embedding(text)
    except Exception as exc:
        sys.stderr.write(f"[hive] collective embedding skip: {exc}\n")
    return None


def publish_lesson(worker_id: int, task_type: str, task_excerpt: str,
                   approach: str, reusable_pattern: str,
                   pitfalls: str = "", evidence: str = "",
                   quality_score: float = 0.5) -> int:
    """娃完成任务后写一条集体记忆。返回 lesson_id。

    副作用: 写 hive.db/collective_lessons 表。
    """
    clean_approach = (approach or "").strip()
    clean_pattern = (reusable_pattern or "").strip()
    if not clean_approach or not clean_pattern:
        raise ValueError("approach/reusable_pattern 不能为空")
    init_db()
    now = _now()
    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO collective_lessons
            (worker_id, task_type, task_excerpt, approach, reusable_pattern, pitfalls, evidence,
             quality_score, created_at, last_used_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
            """,
            (
                int(worker_id), _normalize_task_type(task_type), (task_excerpt or "").strip()[:200],
                clean_approach[:1200], clean_pattern[:1200], (pitfalls or "").strip()[:1200],
                (evidence or "").strip()[:2000], _clamp_score(quality_score), now,
            ),
        )
        return int(cur.lastrowid)


def recall_lessons(task_text: str, task_type: str, k: int = 3,
                   min_quality: float = 0.3) -> list[dict]:
    """派单前查相关经验。返回 top-k lessons。

    排序: quality_score * recency_decay，配合 task_type、关键词和 embedding 相似度重排。
    """
    init_db()
    clean_type = _normalize_task_type(task_type)
    limit = max(1, int(k))
    now = _now()
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM collective_lessons
            WHERE quality_score >= ? AND (task_type = ? OR task_type = 'general')
            ORDER BY quality_score DESC, created_at DESC
            LIMIT ?
            """,
            (_clamp_score(min_quality), clean_type, max(20, limit * 8)),
        ).fetchall()
    lessons = [dict(row) for row in rows]
    if not lessons:
        return []

    query_vec = _encode(task_text or "")
    scored: list[dict[str, Any]] = []
    for lesson in lessons:
        age_days = max(0.0, (now - float(lesson.get("created_at") or now)) / SECONDS_PER_DAY)
        recency_decay = math.exp(-age_days / 45.0)
        type_boost = 1.20 if lesson.get("task_type") == clean_type else 0.90
        keyword_score = _keyword_similarity(task_text or "", lesson)
        semantic_score = 0.0
        if query_vec:
            lesson_text = "\n".join(str(lesson.get(key) or "") for key in (
                "task_excerpt", "approach", "reusable_pattern", "pitfalls", "evidence"
            ))
            semantic_score = max(0.0, _cosine(query_vec, _encode(lesson_text)))
        usage_boost = 1.0 + min(0.2, math.log1p(int(lesson.get("use_count") or 0)) / 20.0)
        quality = float(lesson.get("quality_score") or 0.5)
        lesson["recall_score"] = quality * recency_decay * type_boost * usage_boost * (0.70 + 0.20 * keyword_score + 0.10 * semantic_score)
        scored.append(lesson)
    scored.sort(key=lambda item: item["recall_score"], reverse=True)
    return scored[:limit]


def mark_lesson_used(lesson_id: int, ok: bool = True) -> None:
    """娃调用 lesson 后回报使用结果，并更新 last_used_at。"""
    init_db()
    with _connect() as conn:
        conn.execute(
            """
            UPDATE collective_lessons
            SET use_count = COALESCE(use_count, 0) + 1,
                success_count = COALESCE(success_count, 0) + ?,
                fail_count = COALESCE(fail_count, 0) + ?,
                last_used_at = ?
            WHERE lesson_id = ?
            """,
            (1 if ok else 0, 0 if ok else 1, _now(), int(lesson_id)),
        )


def lessons_to_prompt(lessons: list[dict]) -> str:
    """把 lessons 格式化成可注入娃 prompt 的 markdown 段。"""
    if not lessons:
        return ""
    lines = [f"[集体记忆 - {len(lessons)} 条相关经验]", "以下是蚁后建议参考的集体经验："]
    for index, lesson in enumerate(lessons, start=1):
        quality = float(lesson.get("quality_score") or 0.0)
        used = int(lesson.get("use_count") or 0)
        task_excerpt = str(lesson.get("task_excerpt") or "").strip()[:120]
        lines.append(f"{index}. (quality={quality:.2f}, used {used}x) 任务: {task_excerpt}")
        lines.append(f"   做法: {str(lesson.get('approach') or '').strip()[:500]}")
        lines.append(f"   模式: {str(lesson.get('reusable_pattern') or '').strip()[:500]}")
        pitfalls = str(lesson.get("pitfalls") or "").strip()
        evidence = str(lesson.get("evidence") or "").strip()
        if pitfalls:
            lines.append(f"   坑点: {pitfalls[:300]}")
        if evidence:
            lines.append(f"   证据: {evidence[:300]}")
    return "\n".join(lines)
