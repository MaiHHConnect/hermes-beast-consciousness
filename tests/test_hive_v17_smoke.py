#!/usr/bin/env python3
"""Hive 1.7 task_type pheromone smoke test."""
# === hermes-hive path bootstrap ===
import os
_HERMES_HOME = os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")
_HIVE_DIR = os.path.join(_HERMES_HOME, "hive")
if _HIVE_DIR not in sys.path:
    sys.path.insert(0, _HIVE_DIR)
if _HERMES_HOME not in sys.path:
    sys.path.insert(0, _HERMES_HOME)
# === end bootstrap ===


import os
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes")), ".hermes/hive"))
import hive_pheromones as hp


def main() -> None:
    tmpdir = tempfile.TemporaryDirectory()
    old_db = hp.HIVE_DB
    old_dir = hp.HIVE_DIR
    hp.HIVE_DIR = Path(tmpdir.name)
    hp.HIVE_DB = hp.HIVE_DIR / "hive.db"
    try:
        hp.init_db()
        with sqlite3.connect(str(hp.HIVE_DB)) as conn:
            exists = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='huluwa_task_pheromones'"
            ).fetchone()
        assert exists, "huluwa_task_pheromones table not created"

        for _ in range(3):
            hp.update_huluwa_task_pheromone(2, "code", True)
        for _ in range(2):
            hp.update_huluwa_task_pheromone(2, "code", False, root_cause="timeout")
        row = hp.get_huluwa_task_pheromone(2, "code")
        assert row is not None
        assert abs(row["score"] - 0.45) < 1e-9, row
        assert row["success_count"] == 3, row
        assert row["fail_count"] == 2, row

        hp.update_huluwa_task_pheromone(2, "code", False, root_cause="unknown")
        row2 = hp.get_huluwa_task_pheromone(2, "code")
        assert abs(row2["score"] - 0.45) < 1e-9, row2
        assert row2["fail_count"] == 3, row2

        for _ in range(30):
            hp.update_huluwa_task_pheromone(2, "code", True)
        row3 = hp.get_huluwa_task_pheromone(2, "code")
        assert abs(row3["score"] - 1.0) < 1e-9, row3

        for _ in range(30):
            hp.update_huluwa_task_pheromone(2, "code", False, root_cause="timeout")
        row4 = hp.get_huluwa_task_pheromone(2, "code")
        assert abs(row4["score"] - 0.0) < 1e-9, row4

        print("[v17-smoke] PASS table/update/unknown/clamp")
    finally:
        hp.HIVE_DB = old_db
        hp.HIVE_DIR = old_dir
        tmpdir.cleanup()


if __name__ == "__main__":
    main()
