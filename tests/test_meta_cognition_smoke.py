#!/usr/bin/env python3
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

import json
import sqlite3
import sys
import time
from pathlib import Path

HIVE_DIR = Path(os.environ.get("HERMES_HOME", "~/.hermes")) / ".hermes/hive"
sys.path.insert(0, str(HIVE_DIR))

import hive_emergence as he
from hive_meta_cognition import (
    HIVE_META_DB,
    auto_express_intentions_from_gaps,
    discover_and_act,
    evaluate_intervention,
    express_intention,
    init_meta_cognition_db,
    notice_low_confidence_runs,
    notice_self_gap,
    reflect_on_action,
    refresh_self_model_from_hive_dir,
    update_first_person_state,
    update_self_model,
)


def count(table: str) -> int:
    with sqlite3.connect(str(HIVE_META_DB), timeout=5.0) as conn:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def status(name: str, ok: bool, detail: str = "") -> None:
    print(f"{name}: {'PASS' if ok else 'FAIL'} {detail}".rstrip())
    if not ok:
        raise AssertionError(name)


def seed_capability_gaps() -> None:
    he.init_emergence_db()
    now = time.time()
    with sqlite3.connect(str(HIVE_DIR / "hive.db"), timeout=5.0) as conn:
        for idx in range(3):
            payload = {"huluwa_id": 9 + idx, "task_type": "meta_mock", "score": 0.1, "use_count": 4 + idx}
            conn.execute(
                """
                INSERT INTO emergence_signals
                (signal_type, task_type, scope, payload_json, score, evidence_count, status, created_at)
                VALUES ('capability_gap', 'meta_mock', ?, ?, 0.9, ?, 'discovered', ?)
                """,
                (f"smoke_{idx}_{now}", json.dumps(payload, ensure_ascii=False), 4 + idx, now + idx),
            )
        conn.commit()


def seed_low_confidence_runs() -> None:
    with sqlite3.connect(str(HIVE_DIR / "hive.db"), timeout=5.0) as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS smart_cluster_runs (
            run_id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL DEFAULT '', task_text TEXT NOT NULL DEFAULT '',
            task_type TEXT NOT NULL DEFAULT '', task_scent TEXT NOT NULL DEFAULT '',
            primary_hid INTEGER NOT NULL, fallback_chain_json TEXT NOT NULL DEFAULT '[]',
            confidence REAL NOT NULL DEFAULT 0.0, reasoning TEXT NOT NULL DEFAULT '',
            success_hid INTEGER, success_rate REAL NOT NULL DEFAULT 0.0,
            fallback_used INTEGER NOT NULL DEFAULT 0, duration_ms INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL, finished_at REAL
        );
        """)
        now = time.time()
        for idx in range(3):
            conn.execute(
                """
                INSERT INTO smart_cluster_runs
                (task_id, task_text, task_type, task_scent, primary_hid, fallback_chain_json, confidence, reasoning, created_at)
                VALUES (?, 'mock low confidence', 'mock', 'low_conf_mock', 9, '[9,10,11]', 0.2, 'mock', ?)
                """,
                (f"smoke_low_{idx}_{now}", now + idx),
            )
        conn.commit()


def main() -> None:
    init_meta_cognition_db()

    for idx in range(3):
        reflect_on_action("pheromone_update", {"idx": idx}, "smoke 调权反思", "smoke")
    with sqlite3.connect(str(HIVE_META_DB), timeout=5.0) as conn:
        depths = [r[0] for r in conn.execute("SELECT depth FROM reflection_log ORDER BY id DESC LIMIT 3").fetchall()]
    status("reflection_log", count("reflection_log") >= 3 and depths == [1, 1, 1], f"rows={count('reflection_log')}")

    seed_capability_gaps()
    actions = discover_and_act()
    status("bee_proactive_actions", count("bee_proactive_actions") >= 1 and len(actions) >= 1, f"created={len(actions)} rows={count('bee_proactive_actions')}")

    vals = [(0.2, 0.4), (0.8, 0.7), (0.5, 0.52), (0.1, 0.2), (0.6, 0.54)]
    for idx, (before, after) in enumerate(vals):
        evaluate_intervention(f"smoke-{idx}", before, after, "pheromone")
    with sqlite3.connect(str(HIVE_META_DB), timeout=5.0) as conn:
        verdicts = {r[0] for r in conn.execute("SELECT verdict FROM meta_eval_log WHERE intervention_id LIKE 'smoke-%'").fetchall()}
    status("meta_eval_log", {"helpful", "harmful", "neutral"}.issubset(verdicts), f"verdicts={sorted(verdicts)}")

    express_intention("我想把 1.14 prompt 缩短", 7, "smoke 手动意向")
    auto_ids = auto_express_intentions_from_gaps()
    status("intentions", count("intentions") >= 1 and len(auto_ids) >= 1, f"auto={len(auto_ids)} rows={count('intentions')}")

    for mood, energy in [("calm", 0.8), ("busy", 0.6), ("confused", 0.3)]:
        update_first_person_state(mood, energy, ["mock pain"], ["mock joy"])
    status("first_person_state", count("first_person_state") >= 3, f"rows={count('first_person_state')}")

    seed_low_confidence_runs()
    gap_ids = notice_low_confidence_runs()
    notice_self_gap("我不懂 mock 共识为什么失败", "连续 fail 需要提问", "consensus")
    status("self_gaps", count("self_gaps") >= 1 and len(gap_ids) >= 1, f"auto={len(gap_ids)} rows={count('self_gaps')}")

    update_self_model("smoke_component", "我是 smoke 组件", "2.2", ["mock_dep"])
    model_ids = refresh_self_model_from_hive_dir()
    status("self_model", count("self_model") >= 5 and len(model_ids) >= 5, f"auto={len(model_ids)} rows={count('self_model')}")

    print("META_COGNITION_SMOKE PASS")


if __name__ == "__main__":
    main()
