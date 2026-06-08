#!/usr/bin/env python3
"""
hive_patrol — 蚁后巡视官

每日扫描高频任务，把隐性模式提炼为 collective_lessons，并把高成功 lesson 升级为 swarm_skill。
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

_HERMES_HOME = os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")
_HIVE_DIR = os.path.join(_HERMES_HOME, "hive")
if _HIVE_DIR not in sys.path:
    sys.path.insert(0, _HIVE_DIR)
if _HERMES_HOME not in sys.path:
    sys.path.insert(0, _HERMES_HOME)

sys.path.insert(0, str(Path(__file__).parent))
from hive_collective_memory import HIVE_DB, HIVE_DIR, init_db, publish_lesson
from hive_dispatch import classify_task

LOOKBACK_SECONDS = 86400
MIN_TASK_COUNT = 5


def _connect() -> sqlite3.Connection:
    HIVE_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(HIVE_DB), timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _parse_dispatched_at(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        from datetime import datetime
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return 0.0


def _recent_task_groups() -> dict[str, list[dict[str, Any]]]:
    """扫描 task_history 最近 24h 高频 task_type。"""
    init_db()
    cutoff = time.time() - LOOKBACK_SECONDS
    groups: dict[str, list[dict[str, Any]]] = {}
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT huluwa_id, task_id, task_text, ok, duration_ms, dispatched_at
            FROM task_history
            ORDER BY id DESC
            LIMIT 1000
            """
        ).fetchall()
    for row in rows:
        row_dict = dict(row)
        if _parse_dispatched_at(row_dict.get("dispatched_at")) < cutoff:
            continue
        task_type = classify_task(row_dict.get("task_text") or "")
        groups.setdefault(task_type, []).append(row_dict)
    return {task_type: items for task_type, items in groups.items() if len(items) >= MIN_TASK_COUNT}


def _heuristic_extract(task_type: str, tasks: list[dict[str, Any]]) -> dict[str, str]:
    """LLM 不可用时用启发式提炼经验。"""
    samples = [str(item.get("task_text") or "")[:120] for item in tasks[:5]]
    ok_count = sum(1 for item in tasks if int(item.get("ok") or 0) == 1)
    total = max(1, len(tasks))
    return {
        "approach": f"近 24 小时 {task_type} 类任务出现 {total} 次，成功 {ok_count} 次。优先复用成功任务的输入拆解、快速验证和失败归因流程；先明确目标，再选择专长娃执行，最后用结果证据闭环。样例任务：" + "；".join(samples),
        "reusable_pattern": f"{task_type} 高频任务模式：分类→召回相关经验→派给匹配娃→要求输出证据和 LESSON→成功后沉淀为 collective_lesson。",
        "pitfalls": "不要只更新统计分；必须记录可复用做法、坑点和证据。实时/代码类任务避免无证据结论。",
        "evidence": json.dumps(samples, ensure_ascii=False),
    }


def _llm_extract(task_type: str, tasks: list[dict[str, Any]]) -> dict[str, str]:
    """调用蚁后 LLM 提炼经验，失败时降级启发式。"""
    if not os.getenv("LLM_BASE_URL") and "<LLM_BASE_URL>" == "<LLM_BASE_URL>":
        return _heuristic_extract(task_type, tasks)
    try:
        from hive_consensus import _llm_chat
        prompt = """你是蜂巢蚁后巡视官。请从以下 task_history 提炼 JSON：
{"approach":"300-500字做法", "reusable_pattern":"可复用模式", "pitfalls":"坑点", "evidence":"证据摘要"}
只输出 JSON。

task_type: %s
tasks: %s
""" % (task_type, json.dumps(tasks[:20], ensure_ascii=False))
        result = _llm_chat(prompt, system="你是JSON提炼器，只输出JSON。", timeout=60)
        if not result.get("ok"):
            return _heuristic_extract(task_type, tasks)
        content = str(result.get("content") or "").strip()
        match = re.search(r"\{.*\}", content, flags=re.S)
        data = json.loads(match.group(0) if match else content)
        if data.get("approach") and data.get("reusable_pattern"):
            return {key: str(data.get(key) or "") for key in ("approach", "reusable_pattern", "pitfalls", "evidence")}
    except Exception as exc:
        sys.stderr.write(f"[hive_patrol] llm fallback: {exc}\n")
    return _heuristic_extract(task_type, tasks)


def patrol_collective_lessons() -> int:
    """扫描高频任务并写入蚁后巡视官 lesson。"""
    written = 0
    for task_type, tasks in _recent_task_groups().items():
        data = _llm_extract(task_type, tasks)
        publish_lesson(
            worker_id=0,
            task_type=task_type,
            task_excerpt=f"patrol: last24h {len(tasks)} tasks",
            approach=data["approach"],
            reusable_pattern=data["reusable_pattern"],
            pitfalls=data.get("pitfalls", ""),
            evidence=data.get("evidence", ""),
            quality_score=0.65,
        )
        written += 1
    return written


def _ensure_patrol_signal(conn: sqlite3.Connection, lesson: dict[str, Any]) -> int:
    """为高成功 lesson 建一个可被 hive_emergence 注册的 reproduction signal。"""
    import hive_emergence as he
    he.init_emergence_db()
    pattern = f"collector:{lesson['worker_id']}->analyst:{lesson['worker_id']}->verifier:{lesson['worker_id']}"
    payload = {
        "pattern": pattern,
        "task_type": lesson["task_type"],
        "source": "collective_lesson",
        "lesson_id": lesson["lesson_id"],
        "success_rate": lesson["success_rate"],
    }
    cur = conn.execute(
        """
        INSERT INTO emergence_signals
        (signal_type, task_type, scope, payload_json, score, evidence_count, status, created_at)
        VALUES ('reproduction', ?, 'collective_lesson', ?, ?, ?, 'discovered', ?)
        """,
        (lesson["task_type"], json.dumps(payload, ensure_ascii=False), float(lesson["success_rate"]), int(lesson["use_count"]), time.time()),
    )
    return int(cur.lastrowid)


def promote_successful_lessons() -> int:
    """把 use_count>=5 且 success_rate>0.7 的 lesson 升级为 swarm_skill。"""
    init_db()
    promoted = 0
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT *, CAST(success_count AS REAL) / MAX(1, success_count + fail_count) AS success_rate
            FROM collective_lessons
            WHERE promoted_to_skill IS NULL AND use_count >= 5
+              AND CAST(success_count AS REAL) / MAX(1, success_count + fail_count) > 0.7
            ORDER BY quality_score DESC, use_count DESC
            LIMIT 20
            """.replace("+              ", "              ")
        ).fetchall()
        for row in rows:
            lesson = dict(row)
            try:
                import hive_emergence as he
                signal_id = _ensure_patrol_signal(conn, lesson)
                conn.commit()
                ok = he.register_swarm_skill(signal_id)
                skill_name = f"collective_lesson_{lesson['lesson_id']}" if ok else "promotion_failed"
                conn.execute(
                    "UPDATE collective_lessons SET promoted_to_skill = ? WHERE lesson_id = ?",
                    (skill_name, int(lesson["lesson_id"])),
                )
                promoted += 1 if ok else 0
            except Exception as exc:
                sys.stderr.write(f"[hive_patrol] promote fail: {exc}\n")
    return promoted


def run_patrol() -> dict[str, int]:
    """执行一次蚁后巡视。"""
    lessons = patrol_collective_lessons()
    promoted = promote_successful_lessons()
    print(f"[hive_patrol] lessons={lessons} promoted={promoted}")
    return {"lessons": lessons, "promoted": promoted}


if __name__ == "__main__":
    run_patrol()
