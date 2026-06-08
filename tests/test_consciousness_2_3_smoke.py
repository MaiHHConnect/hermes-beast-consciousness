#!/usr/bin/env python3
"""蜂巢 2.3 意识增强 smoke test：不跑 gpt-5.5."""
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

import random
import sqlite3
import sys
import time
from pathlib import Path

HIVE_DIR = Path(os.environ.get("HERMES_HOME", "~/.hermes")) / ".hermes/hive"
HIVE_DB = HIVE_DIR / "hive.db"
HIVE_META_DB = HIVE_DIR / "hive_meta.db"
sys.path.insert(0, str(HIVE_DIR))

from hive_consciousness_2_3 import (  # noqa: E402
    apply_forgetting_curve,
    dream,
    imagine,
    init_consciousness_db,
    sleep_cycle,
)
from hive_smart_cluster import pick_smart_cluster  # noqa: E402


def count_rows(table: str) -> int:
    with sqlite3.connect(str(HIVE_META_DB), timeout=5.0) as conn:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def ensure_seed_signals() -> None:
    with sqlite3.connect(str(HIVE_DB), timeout=5.0) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS emergence_signals (
                signal_id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_type TEXT NOT NULL,
                task_type TEXT,
                scope TEXT,
                payload_json TEXT NOT NULL,
                score REAL NOT NULL DEFAULT 0.0,
                evidence_count INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'discovered',
                review_note TEXT,
                created_at REAL NOT NULL,
                promoted_at REAL,
                last_reinforced_at REAL
            )
        """)
        n = conn.execute("SELECT COUNT(*) FROM emergence_signals").fetchone()[0]
        for idx in range(max(0, 2 - n)):
            conn.execute("""
                INSERT INTO emergence_signals
                (signal_type, task_type, scope, payload_json, score, evidence_count, status, created_at, last_reinforced_at)
                VALUES (?, ?, ?, ?, ?, ?, 'discovered', ?, ?)
            """, ("smoke_signal", f"stock_{idx}", "smoke", "{}", 0.2, 1, time.time() - 40 * 86400, time.time() - 40 * 86400))


def assert_true(name: str, cond: bool) -> None:
    if not cond:
        raise AssertionError(name)
    print(f"PASS {name}")


def main() -> int:
    random.seed(0)
    init_consciousness_db()
    ensure_seed_signals()

    before = count_rows("free_will_log")
    for _ in range(1000):
        pick_smart_cluster("请分析 stock 财经数据并给出代码方案", "finance", debug=False)
    free_count = count_rows("free_will_log") - before
    assert_true("free_will_log 1%-3%", 10 <= free_count <= 30)

    imagine("如果让 9 娃专门跑 stock", "新增 stock 专家")
    assert_true("imagination_log >= 1", count_rows("imagination_log") >= 1)

    apply_forgetting_curve(decay_rate=0.05, threshold=0.1)
    assert_true("forgetting_log >= 1", count_rows("forgetting_log") >= 1)

    sleep_cycle(duration_min=0.01)
    assert_true("sleep_log >= 1", count_rows("sleep_log") >= 1)

    made = []
    for _ in range(3):
        made.extend(dream())
    assert_true("dream_journal >= 2", count_rows("dream_journal") >= 2 and len(made) >= 2)

    print("PASS consciousness_2_3_smoke")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"FAIL consciousness_2_3_smoke: {exc}")
        raise
