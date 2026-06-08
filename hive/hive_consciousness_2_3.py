from __future__ import annotations
#!/usr/bin/env python3
"""hive_consciousness_2_3 — 蜂巢 2.3 意识增强旁路层."""
# === hermes-hive path bootstrap ===
import os as _os
import sys as _sys
_HERMES_HOME = _os.environ.get("HERMES_HOME") or _os.path.expanduser("~/.hermes")
_HIVE_DIR = _os.path.join(_HERMES_HOME, "hive")
if _HIVE_DIR not in _sys.path:
    _sys.path.insert(0, _HIVE_DIR)
if _HERMES_HOME not in _sys.path:
    _sys.path.insert(0, _HERMES_HOME)
# === end bootstrap ===



import difflib
import json
import random
import sqlite3
import time
from pathlib import Path
from typing import Any

HIVE_DIR = Path(os.environ.get("HERMES_HOME", "~/.hermes")) / ".hermes/hive"
HIVE_DB = HIVE_DIR / "hive.db"
HIVE_META_DB = HIVE_DIR / "hive_meta.db"
SMART_HIDS = [9, 10, 11, 12, 13]

DDL = """
CREATE TABLE IF NOT EXISTS free_will_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    primary_decision TEXT NOT NULL,
    alt_decision TEXT NOT NULL,
    p_explore REAL NOT NULL,
    reason TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS imagination_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    scenario TEXT NOT NULL,
    plan TEXT NOT NULL,
    predicted_impacts_json TEXT NOT NULL,
    risks_json TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS forgetting_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    table_name TEXT NOT NULL,
    rows_decayed INTEGER NOT NULL,
    decay_rate REAL NOT NULL,
    threshold REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sleep_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    duration_min REAL NOT NULL,
    kb_dedup_count INTEGER NOT NULL,
    gaps_archived INTEGER NOT NULL,
    intentions_cleaned INTEGER NOT NULL,
    woke_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS dream_journal (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    signal_a_id INTEGER,
    signal_b_id INTEGER,
    dream_pattern TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'fresh' CHECK(status IN ('fresh','dismissed','promoted')),
    linked_intention_id INTEGER
);
"""


def _now() -> float:
    return time.time()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _connect_meta() -> sqlite3.Connection:
    HIVE_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(HIVE_META_DB), timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _connect_hive() -> sqlite3.Connection:
    HIVE_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(HIVE_DB), timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    return bool(row)


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    if not _table_exists(conn, table):
        return set()
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def init_consciousness_db() -> None:
    """创建 2.3 五张表，并兼容 emergence_signals.last_reinforced_at."""
    with _connect_meta() as conn:
        conn.executescript(DDL)
    with _connect_hive() as conn:
        cols = _columns(conn, "emergence_signals")
        if cols and "last_reinforced_at" not in cols:
            conn.execute("ALTER TABLE emergence_signals ADD COLUMN last_reinforced_at REAL")


def maybe_explore(decision: dict, p: float = 0.01) -> dict:
    """ε-greedy 自由意志：p 概率从 9~13 非 top1 随机选 alt."""
    init_consciousness_db()
    if not isinstance(decision, dict) or random.random() >= p:
        return decision
    primary = int(decision.get("primary") or 0)
    choices = [hid for hid in SMART_HIDS if hid != primary]
    if not choices:
        return decision
    alt = random.choice(choices)
    explored = dict(decision)
    explored["primary"] = alt
    chain = [alt] + [hid for hid in decision.get("fallback_chain", []) if hid != alt]
    explored["fallback_chain"] = chain[:3]
    explored["reasoning"] = f"ε-greedy探索: 原top1={primary}, 试选{alt}; " + str(decision.get("reasoning", ""))
    with _connect_meta() as conn:
        conn.execute("INSERT INTO free_will_log (ts, primary_decision, alt_decision, p_explore, reason) VALUES (?, ?, ?, ?, ?)",
                     (_now(), _json(decision), _json(explored), float(p), "1% non-top1 smart-cluster exploration"))
    return explored


def imagine(scenario_text: str, plan_text: str) -> dict:
    """dry-run 预测改动影响，不写业务状态、不跑娃."""
    init_consciousness_db()
    text = f"{scenario_text} {plan_text}".lower()
    affected = [hid for hid in SMART_HIDS if str(hid) in text or any(k in text for k in ["stock", "财经", "代码", "code"])] or SMART_HIDS
    pheromone_direction = "up" if any(k in text for k in ["新增", "专门", "优化", "expert", "专家"]) else "mixed"
    chain_stats = "specialization may improve matching; fallback diversity may drop"
    risks = ["过拟合单一任务气味", "非目标任务路由偏置"] if pheromone_direction == "up" else ["收益不确定"]
    predicted = {"affected_huluwa": affected, "pheromone_direction": pheromone_direction, "chain_stats_impact": chain_stats, "side_effects": risks}
    with _connect_meta() as conn:
        conn.execute("INSERT INTO imagination_log (ts, scenario, plan, predicted_impacts_json, risks_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                     (_now(), scenario_text, plan_text, _json(predicted), _json(risks), _now()))
    return predicted


def apply_forgetting_curve(decay_rate: float = 0.05, threshold: float = 0.1) -> dict:
    """低分 pheromone 与 30 天未强化 emergence signal 衰减."""
    init_consciousness_db()
    results: dict[str, int] = {}
    with _connect_hive() as conn:
        ptable = "hive_pheromones" if _table_exists(conn, "hive_pheromones") else "huluwa_task_pheromones"
        if _table_exists(conn, ptable) and "score" in _columns(conn, ptable):
            cur = conn.execute(f"UPDATE {ptable} SET score = score * ? WHERE score < ?", (1.0 - decay_rate, threshold))
            results[ptable] = cur.rowcount if cur.rowcount >= 0 else 0
        if _table_exists(conn, "emergence_signals"):
            cutoff = _now() - 30 * 86400
            cur = conn.execute("UPDATE emergence_signals SET score = score * ? WHERE COALESCE(last_reinforced_at, created_at, 0) < ?",
                               (1.0 - decay_rate, cutoff))
            results["emergence_signals"] = cur.rowcount if cur.rowcount >= 0 else 0
    with _connect_meta() as conn:
        for table, count in results.items() or {"none": 0}.items():
            conn.execute("INSERT INTO forgetting_log (ts, table_name, rows_decayed, decay_rate, threshold) VALUES (?, ?, ?, ?, ?)",
                         (_now(), table, int(count), float(decay_rate), float(threshold)))
    return results


def _dedup_kb() -> int:
    if not HIVE_DB.exists():
        return 0
    with _connect_hive() as conn:
        tables = ["kb_items", "knowledge_items"]
        for table in tables:
            cols = _columns(conn, table)
            text_col = "content" if "content" in cols else ("text" if "text" in cols else None)
            id_col = "id" if "id" in cols else ("item_id" if "item_id" in cols else None)
            if not text_col or not id_col:
                continue
            rows = conn.execute(f"SELECT {id_col}, {text_col} FROM {table} WHERE {text_col} IS NOT NULL LIMIT 500").fetchall()
            removed = 0
            for idx, (left_id, left_text) in enumerate(rows):
                for right_id, right_text in rows[idx + 1:]:
                    if difflib.SequenceMatcher(None, str(left_text), str(right_text)).ratio() > 0.95:
                        conn.execute(f"DELETE FROM {table} WHERE {id_col}=?", (right_id,)); removed += 1
            return removed
    return 0


def dream() -> list[dict]:
    """组合 2 个旧 emergence_signal，写 dream_journal 与 asleep intention."""
    init_consciousness_db()
    with _connect_hive() as hconn:
        if not _table_exists(hconn, "emergence_signals"):
            return []
        rows = hconn.execute("SELECT signal_id, signal_type, task_type, payload_json, score FROM emergence_signals ORDER BY (score < 0.5) DESC, RANDOM() LIMIT 2").fetchall()
    if len(rows) < 2:
        return []
    a, b = rows
    pattern = f"梦幻pattern: {a[1]}:{a[2] or 'general'} + {b[1]}:{b[2] or 'general'} → 用9~13智集群交叉验证"
    with _connect_meta() as mconn:
        cur = mconn.execute("INSERT INTO intentions (ts, goal, priority, rationale, status) VALUES (?, ?, ?, ?, ?)",
                            (_now(), pattern, 3, "dream_journal asleep pattern", "asleep"))
        iid = cur.lastrowid
        jid = mconn.execute("INSERT INTO dream_journal (ts, signal_a_id, signal_b_id, dream_pattern, status, linked_intention_id) VALUES (?, ?, ?, ?, 'fresh', ?)",
                            (_now(), a[0], b[0], pattern, iid)).lastrowid
    return [{"dream_id": jid, "signal_a_id": a[0], "signal_b_id": b[0], "dream_pattern": pattern, "linked_intention_id": iid}]


def sleep_cycle(duration_min: float = 1) -> dict:
    """模拟睡眠：KB 去重、归档 resolved gaps、清理 abandoned intentions、做梦."""
    init_consciousness_db()
    ts = _now(); time.sleep(2)
    kb_dedup = _dedup_kb(); cutoff7 = _now() - 7 * 86400; cutoff30 = _now() - 30 * 86400
    dreams = []
    for _ in range(random.randint(3, 5)):
        dreams.extend(dream())
    with _connect_meta() as conn:
        gap_row = conn.execute("SELECT COUNT(*) FROM self_gaps WHERE resolved=1 AND COALESCE(resolved_at, ts) < ?", (cutoff7,)).fetchone()
        gaps_archived = int(gap_row[0] if gap_row else 0)
        icur = conn.execute("DELETE FROM intentions WHERE status='abandoned' AND ts < ?", (cutoff30,))
        intentions_cleaned = max(icur.rowcount, 0)
        woke = _now()
        conn.execute("INSERT INTO sleep_log (ts, duration_min, kb_dedup_count, gaps_archived, intentions_cleaned, woke_at) VALUES (?, ?, ?, ?, ?, ?)",
                     (ts, float(duration_min), kb_dedup, gaps_archived, intentions_cleaned, woke))
    return {"kb_dedup_count": kb_dedup, "gaps_archived": gaps_archived, "intentions_cleaned": intentions_cleaned, "dreams": len(dreams)}


def consciousness_tick() -> dict:
    """元认知末尾旁路：基于当日 self_gaps 自动 imagine 一次."""
    init_consciousness_db()
    try:
        with _connect_meta() as conn:
            cutoff = _now() - 86400
            rows = conn.execute("SELECT question, related_capability FROM self_gaps WHERE ts >= ? ORDER BY ts DESC LIMIT 3", (cutoff,)).fetchall()
        scenario = "今日self_gaps: " + "; ".join([r[0] for r in rows]) if rows else "今日无新增self_gaps"
        plan = "针对高频gap调整9~13智集群路由与专家分工"
        return {"ok": True, "imagination": imagine(scenario, plan)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


if __name__ == "__main__":
    init_consciousness_db()
    print("hive_consciousness_2_3 schema ready")