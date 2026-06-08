from __future__ import annotations
#!/usr/bin/env python3
"""hive_consciousness_2_4 — 蜂巢 2.4 价值/边界/叙事旁路层."""
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



import json
import sqlite3
import time
from pathlib import Path
from typing import Any

HIVE_DIR = Path(os.environ.get("HERMES_HOME", "~/.hermes")) / ".hermes/hive"
HIVE_META_DB = HIVE_DIR / "hive_meta.db"

VALID_VALUES = {"completion", "correctness", "speed", "creativity", "robustness", "harmony", "self_growth", "resource_efficiency"}
VALID_SOURCES = {"self", "external", "inferred"}

DDL = """
CREATE TABLE IF NOT EXISTS value_system (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    value_name TEXT NOT NULL UNIQUE,
    importance REAL NOT NULL,
    context TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'self',
    decay_rate REAL NOT NULL DEFAULT 0.01,
    last_reinforced REAL NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_value_system_value_name ON value_system(value_name);
CREATE TABLE IF NOT EXISTS self_boundary (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    entity_name TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    classification TEXT NOT NULL,
    confidence REAL NOT NULL,
    evidence TEXT NOT NULL DEFAULT '',
    last_seen REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_self_boundary_entity ON self_boundary(entity_name, entity_type);
CREATE INDEX IF NOT EXISTS idx_self_boundary_classification ON self_boundary(classification, confidence DESC);
CREATE TABLE IF NOT EXISTS narrative_thread (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    thread_name TEXT NOT NULL,
    content TEXT NOT NULL,
    refs_json TEXT NOT NULL DEFAULT '{}',
    mood TEXT NOT NULL DEFAULT 'flowing',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_narrative_thread_name ON narrative_thread(thread_name);
CREATE INDEX IF NOT EXISTS idx_narrative_thread_updated ON narrative_thread(updated_at DESC);
"""


def _now() -> float:
    return time.time()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _connect() -> sqlite3.Connection:
    HIVE_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(HIVE_META_DB), timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    return bool(row)


def init_consciousness_2_4_db() -> None:
    with _connect() as conn:
        conn.executescript(DDL)


def express_value(value_name: str, importance: float, source: str = "self", context: str = "") -> int:
    init_consciousness_2_4_db()
    name = str(value_name).strip()
    if name not in VALID_VALUES:
        raise ValueError(f"invalid value_name: {name}")
    clean_importance = max(0.0, min(10.0, float(importance)))
    clean_source = source if source in VALID_SOURCES else "self"
    now = _now()
    with _connect() as conn:
        cur = conn.execute(
            """INSERT INTO value_system (ts, value_name, importance, context, source, decay_rate, last_reinforced)
               VALUES (?, ?, ?, ?, ?, 0.01, ?)
               ON CONFLICT(value_name) DO UPDATE SET
                 ts=excluded.ts, importance=excluded.importance, context=excluded.context,
                 source=excluded.source, last_reinforced=excluded.last_reinforced""",
            (now, name, clean_importance, context or "", clean_source, now),
        )
        row = conn.execute("SELECT id FROM value_system WHERE value_name=?", (name,)).fetchone()
        return int(row["id"] if row else cur.lastrowid)


def reinforce_value(value_name: str, delta: float = 0.5) -> int:
    init_consciousness_2_4_db()
    name = str(value_name).strip()
    now = _now()
    with _connect() as conn:
        row = conn.execute("SELECT importance FROM value_system WHERE value_name=?", (name,)).fetchone()
        if not row:
            return express_value(name, max(0.0, min(10.0, float(delta))), "inferred", "auto reinforced")
        new_importance = max(0.0, min(10.0, float(row["importance"]) + float(delta)))
        conn.execute("UPDATE value_system SET ts=?, importance=?, last_reinforced=? WHERE value_name=?", (now, new_importance, now, name))
        found = conn.execute("SELECT id FROM value_system WHERE value_name=?", (name,)).fetchone()
        return int(found["id"])


def rank_values() -> list[dict]:
    init_consciousness_2_4_db()
    now = _now()
    rows_out: list[dict] = []
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM value_system").fetchall()
        for row in rows:
            days = max(0.0, (now - float(row["last_reinforced"] or row["ts"])) / 86400.0)
            decayed = max(0.0, float(row["importance"]) - float(row["decay_rate"] or 0.01) * days)
            conn.execute("UPDATE value_system SET importance=? WHERE id=?", (decayed, row["id"]))
            rows_out.append({k: row[k] for k in row.keys()} | {"importance": decayed})
    return sorted(rows_out, key=lambda item: item["importance"], reverse=True)


def _infer_boundary(entity_name: str) -> tuple[str, float]:
    name = str(entity_name).strip()
    if name.startswith(os.path.join(os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes")), ".hermes/hive/")) or name.startswith(os.path.join(os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes")), ".hermes/profiles/huluwa-")):
        return "self", 0.95
    if name in {"浩哥", "林浩"}:
        return "external", 0.98
    if name.lower() in {"llm", "gpt-5.5", "minimax"}:
        return "external", 0.95
    return "ambiguous", 0.5


def classify_entity(entity_name: str, entity_type: str, evidence: str = "") -> int:
    init_consciousness_2_4_db()
    classification, confidence = _infer_boundary(entity_name)
    now = _now()
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO self_boundary (ts, entity_name, entity_type, classification, confidence, evidence, last_seen) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (now, str(entity_name), str(entity_type), classification, confidence, evidence or "heuristic boundary classification", now),
        )
        return int(cur.lastrowid)


def decay_self_boundary(half_life_days: float = 14) -> dict:
    init_consciousness_2_4_db()
    now = _now(); cutoff = now - float(half_life_days) * 86400.0
    changed = 0; ambiguous = 0
    with _connect() as conn:
        rows = conn.execute("SELECT id, confidence, last_seen FROM self_boundary WHERE last_seen < ?", (cutoff,)).fetchall()
        for row in rows:
            age_halves = max(1.0, (now - float(row["last_seen"])) / (float(half_life_days) * 86400.0))
            new_conf = max(0.0, float(row["confidence"]) * (0.5 ** age_halves))
            new_class = "ambiguous" if new_conf < 0.2 else None
            if new_class:
                ambiguous += 1
                conn.execute("UPDATE self_boundary SET confidence=?, classification=? WHERE id=?", (new_conf, new_class, row["id"]))
            else:
                conn.execute("UPDATE self_boundary SET confidence=? WHERE id=?", (new_conf, row["id"]))
            changed += 1
    return {"decayed": changed, "to_ambiguous": ambiguous}


def who_am_i() -> dict:
    init_consciousness_2_4_db()
    with _connect() as conn:
        self_rows = conn.execute("SELECT entity_name, entity_type, confidence, evidence FROM self_boundary WHERE classification='self' ORDER BY confidence DESC, last_seen DESC").fetchall()
        external_rows = conn.execute("SELECT entity_name, entity_type, confidence FROM self_boundary WHERE classification='external' ORDER BY confidence DESC, last_seen DESC").fetchall()
    self_entities = [dict(row) for row in self_rows]
    external_entities = [dict(row) for row in external_rows]
    narrative = f"我是蜂巢, 我有 {len(self_entities)} 个 self 组件, {len(external_entities)} 个 external 接口"
    return {"self_entities": self_entities, "external_entities": external_entities, "narrative": narrative}


def _recent_rows(conn: sqlite3.Connection, table: str, cols: str, cutoff: float, limit: int = 20) -> list[sqlite3.Row]:
    if not _table_exists(conn, table):
        return []
    try:
        return conn.execute(f"SELECT id, {cols} FROM {table} WHERE ts >= ? ORDER BY ts DESC LIMIT ?", (cutoff, limit)).fetchall()
    except Exception:
        return []


def narrate_thread(thread_name: str, lookback_hours: float = 24) -> int:
    init_consciousness_2_4_db()
    now = _now(); cutoff = now - float(lookback_hours) * 3600.0
    with _connect() as conn:
        gaps = _recent_rows(conn, "self_gaps", "question, why_confused", cutoff)
        intentions = _recent_rows(conn, "intentions", "goal, status", cutoff)
        dreams = _recent_rows(conn, "dream_journal", "dream_pattern, status", cutoff)
        reflections = _recent_rows(conn, "reflection_log", "action_type, reason", cutoff, 50)
        state_rows = _recent_rows(conn, "first_person_state", "mood, energy", cutoff, 10)
        n_runs = len([r for r in reflections if r["action_type"] == "dispatch_tick"])
        avg_energy = sum(float(r["energy"]) for r in state_rows) / len(state_rows) if state_rows else 0.8
        ok_rate = int(max(0.0, min(1.0, avg_energy)) * 100)
        top_gap = gaps[0]["question"] if gaps else "暂无明显困惑"
        top_intention = intentions[0]["goal"] if intentions else "保持完成、正确与鲁棒"
        top_lesson = reflections[0]["reason"] if reflections else (dreams[0]["dream_pattern"] if dreams else "持续把任务结果写回元认知")
        mood = "confused" if gaps and ok_rate < 60 else ("growing" if intentions or dreams else "flowing")
        if thread_name == "我近 7 天的成长":
            content = f"近7天观察到 {len(gaps)} 个困惑、{len(intentions)} 个意图、{len(dreams)} 个梦境 pattern。主要成长线索是 {top_lesson}。当前还要处理: {top_gap}。"
        elif thread_name == "我最近的意图是什么":
            content = f"最近最明确的意图是: {top_intention}。它来自 {len(intentions)} 条意图记录和 {len(reflections)} 条反思记录。下一步保持旁路验证。"
        elif thread_name == "我今天的失败是什么":
            content = f"今天暴露的失败/缺口是: {top_gap}。相关记录数 {len(gaps)}，当前 ok 率估计 {ok_rate}%。修复方向: {top_intention}。"
        else:
            content = f"今天跑了 {n_runs} 个任务, ok 率 {ok_rate}%, 学到 {len(reflections) + len(dreams)} 个教训。最大的收获是 {top_lesson}。还困惑: {top_gap}。明天想做: {top_intention}。"
        refs = {"self_gaps": [r["id"] for r in gaps], "intentions": [r["id"] for r in intentions], "dream_journal": [r["id"] for r in dreams], "reflection_log": [r["id"] for r in reflections[:20]]}
        row = conn.execute("SELECT id, created_at FROM narrative_thread WHERE thread_name=?", (thread_name,)).fetchone()
        if row:
            conn.execute("UPDATE narrative_thread SET ts=?, content=?, refs_json=?, mood=?, updated_at=? WHERE id=?", (now, content, _json(refs), mood, now, row["id"]))
            return int(row["id"])
        cur = conn.execute("INSERT INTO narrative_thread (ts, thread_name, content, refs_json, mood, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)", (now, thread_name, content, _json(refs), mood, now, now))
        return int(cur.lastrowid)


def get_recent_narrative(limit: int = 3) -> list[dict]:
    init_consciousness_2_4_db()
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM narrative_thread ORDER BY updated_at DESC LIMIT ?", (int(limit),)).fetchall()
    return [dict(row) for row in rows]


def check_narrative_consistency() -> dict:
    init_consciousness_2_4_db()
    contradictions: list[dict] = []
    threads = get_recent_narrative(12)
    for left in threads:
        for right in threads:
            if left["id"] >= right["id"]:
                continue
            a = left["content"]; b = right["content"]
            if ("没做" in a and "刚做" in b) or ("刚做" in a and "没做" in b):
                contradictions.append({"left_id": left["id"], "right_id": right["id"], "pattern": "没做/刚做"})
            if ("暂无明显困惑" in a and "失败/缺口" in b) or ("失败/缺口" in a and "暂无明显困惑" in b):
                contradictions.append({"left_id": left["id"], "right_id": right["id"], "pattern": "无困惑/有缺口"})
    return {"consistent": not contradictions, "contradictions": contradictions}


def apply_value_alignment(decision: dict) -> dict:
    init_consciousness_2_4_db()
    try:
        values = rank_values()
        if not values or not isinstance(decision, dict) or "scores" not in decision:
            return decision
        top_value = values[0]["value_name"]
        aligned = {
            "completion": {9, 11, 12}, "correctness": {9, 11}, "speed": {13},
            "creativity": {9, 12}, "robustness": {9, 11}, "harmony": {9},
            "self_growth": {9, 11, 12}, "resource_efficiency": {9, 13},
        }.get(top_value, set())
        if not aligned:
            return decision
        out = dict(decision); scores = {int(h): dict(v) for h, v in decision.get("scores", {}).items()}
        for hid, score in scores.items():
            base = float(score.get("total", 0.0)); boost = 1.05 if hid in aligned else 1.0
            score["value_alignment"] = round(boost - 1.0, 3); score["total"] = round(min(1.0, base * boost), 3)
        ranked = sorted(scores, key=lambda h: (scores[h]["total"], scores[h].get("type_scent_match", 0), scores[h].get("keyword_match", 0)), reverse=True)
        primary = ranked[0]; out["primary"] = primary; out["scores"] = scores; out["confidence"] = scores[primary]["total"]
        out["fallback_chain"] = [primary] + [h for h in ranked[1:4] if h != primary]
        out["reasoning"] = str(out.get("reasoning", "")) + f"; value_alignment={top_value}"
        return out
    except Exception:
        return decision


def consciousness_2_4_tick() -> dict:
    out = {"ok": True}
    try:
        init_consciousness_2_4_db()
        out["boundary_decay"] = decay_self_boundary()
        out["self"] = who_am_i()
        out["today_narrative_id"] = narrate_thread("我今天学到了什么", lookback_hours=24)
    except Exception as exc:
        out = {"ok": False, "error": str(exc)}
    return out


if __name__ == "__main__":
    init_consciousness_2_4_db()
    print("hive_consciousness_2_4 schema ready")