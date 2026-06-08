#!/usr/bin/env python3
"""hive_meta_cognition — 蜂巢 2.2 元认知层入口."""
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
import time
from pathlib import Path
from typing import Any, List
from hive_consciousness_2_3 import consciousness_tick
from hive_consciousness_2_4 import consciousness_2_4_tick

HIVE_DIR = Path(os.environ.get("HERMES_HOME", "~/.hermes")) / ".hermes/hive"
HIVE_DB = HIVE_DIR / "hive.db"
HIVE_META_DB = HIVE_DIR / "hive_meta.db"


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


def init_meta_cognition_db() -> None:
    """一次性创建 7 张元认知表；不做迁移."""
    with _connect() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS reflection_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL,
            action_type TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            reason TEXT NOT NULL,
            context TEXT NOT NULL DEFAULT '',
            depth INTEGER NOT NULL DEFAULT 1
        );
        CREATE INDEX IF NOT EXISTS idx_reflection_log_ts ON reflection_log(ts);
        CREATE INDEX IF NOT EXISTS idx_reflection_log_action ON reflection_log(action_type, ts);

        CREATE TABLE IF NOT EXISTS bee_proactive_actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL,
            signal_id TEXT NOT NULL DEFAULT '',
            action_type TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            result TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_bee_proactive_actions_ts ON bee_proactive_actions(ts);
        CREATE INDEX IF NOT EXISTS idx_bee_proactive_actions_status ON bee_proactive_actions(status, ts);
        CREATE INDEX IF NOT EXISTS idx_bee_proactive_actions_signal ON bee_proactive_actions(signal_id);

        CREATE TABLE IF NOT EXISTS meta_eval_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL,
            intervention_id TEXT NOT NULL,
            before_score REAL NOT NULL,
            after_score REAL NOT NULL,
            delta REAL NOT NULL,
            verdict TEXT NOT NULL,
            intervention_type TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_meta_eval_log_ts ON meta_eval_log(ts);
        CREATE INDEX IF NOT EXISTS idx_meta_eval_log_intervention ON meta_eval_log(intervention_id);
        CREATE INDEX IF NOT EXISTS idx_meta_eval_log_verdict ON meta_eval_log(verdict, ts);

        CREATE TABLE IF NOT EXISTS intentions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL,
            goal TEXT NOT NULL,
            priority INTEGER NOT NULL,
            rationale TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active'
        );
        CREATE INDEX IF NOT EXISTS idx_intentions_ts ON intentions(ts);
        CREATE INDEX IF NOT EXISTS idx_intentions_status ON intentions(status, priority DESC);

        CREATE TABLE IF NOT EXISTS first_person_state (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL,
            mood TEXT NOT NULL,
            energy REAL NOT NULL,
            pain_json TEXT NOT NULL,
            joy_json TEXT NOT NULL,
            narrative TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_first_person_state_ts ON first_person_state(ts);
        CREATE INDEX IF NOT EXISTS idx_first_person_state_mood ON first_person_state(mood, ts);

        CREATE TABLE IF NOT EXISTS self_gaps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL,
            question TEXT NOT NULL,
            why_confused TEXT NOT NULL,
            related_capability TEXT NOT NULL DEFAULT '',
            urgency TEXT NOT NULL DEFAULT 'med',
            resolved INTEGER NOT NULL DEFAULT 0,
            resolved_at REAL
        );
        CREATE INDEX IF NOT EXISTS idx_self_gaps_ts ON self_gaps(ts);
        CREATE INDEX IF NOT EXISTS idx_self_gaps_resolved ON self_gaps(resolved, urgency, ts);
        CREATE INDEX IF NOT EXISTS idx_self_gaps_capability ON self_gaps(related_capability);

        CREATE TABLE IF NOT EXISTS self_model (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL,
            component TEXT NOT NULL,
            description TEXT NOT NULL,
            version TEXT NOT NULL,
            deps_json TEXT NOT NULL,
            last_verified REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_self_model_ts ON self_model(ts);
        CREATE INDEX IF NOT EXISTS idx_self_model_component ON self_model(component, ts);
        """)


def reflect_on_action(action_type: str, action_payload: dict, reason: str, context: str = "") -> int:
    """每次调权/路由/swarm_skill 触发时调用, 写一行反思记录."""
    init_meta_cognition_db()
    depth = 2 if str(action_type).startswith("reflect") else 1
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO reflection_log (ts, action_type, payload_json, reason, context, depth) VALUES (?, ?, ?, ?, ?, ?)",
            (_now(), action_type, _json(action_payload or {}), reason, context or "", depth),
        )
        return int(cur.lastrowid)


def discover_and_act() -> List[dict]:
    """蜂巢自己扫信号, capability_gap > 3 时创建 pending 招募动作."""
    init_meta_cognition_db()
    gaps: list[dict] = []
    if HIVE_DB.exists():
        try:
            with sqlite3.connect(str(HIVE_DB), timeout=5.0) as conn:
                rows = conn.execute("""
                    SELECT signal_id, task_type, payload_json, score, evidence_count
                    FROM emergence_signals
                    WHERE signal_type='capability_gap' AND status IN ('discovered','reviewed')
                      AND evidence_count > 3
                    ORDER BY score DESC, created_at DESC LIMIT 20
                """).fetchall()
            for sid, task_type, payload, score, evidence in rows:
                gaps.append({"signal_id": f"emergence:{sid}", "task_type": task_type, "payload": json.loads(payload or "{}"), "score": score, "evidence": evidence})
        except Exception:
            pass
    with _connect() as conn:
        rows = conn.execute("""
            SELECT id, related_capability, question, urgency FROM self_gaps
            WHERE resolved=0 AND urgency IN ('med','high') ORDER BY ts DESC LIMIT 20
        """).fetchall()
    for row in rows:
        gaps.append({"signal_id": f"self_gap:{row['id']}", "task_type": row['related_capability'], "payload": {"question": row['question'], "urgency": row['urgency']}, "score": 0.7, "evidence": 4})
    created: list[dict] = []
    with _connect() as conn:
        for gap in gaps:
            if int(gap.get("evidence") or 0) <= 3:
                continue
            exists = conn.execute("SELECT id FROM bee_proactive_actions WHERE signal_id=? AND status IN ('pending','running')", (gap["signal_id"],)).fetchone()
            if exists:
                continue
            payload = {"intent": "recruit_new_huluwa", "task_type": gap.get("task_type") or "unknown", "source": gap}
            cur = conn.execute(
                "INSERT INTO bee_proactive_actions (ts, signal_id, action_type, payload_json, status, result) VALUES (?, ?, ?, ?, 'pending', '')",
                (_now(), gap["signal_id"], "recruit_new_huluwa", _json(payload)),
            )
            created.append({"id": int(cur.lastrowid), "status": "pending", **payload})
    return created


def evaluate_intervention(intervention_id: str, before_score: float, after_score: float, intervention_type: str) -> int:
    """评估这次调权/路由/链路干预是否有效."""
    init_meta_cognition_db()
    before = float(before_score)
    after = float(after_score)
    delta = after - before
    verdict = "helpful" if delta > 0.05 else ("harmful" if delta < -0.05 else "neutral")
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO meta_eval_log (ts, intervention_id, before_score, after_score, delta, verdict, intervention_type) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (_now(), str(intervention_id), before, after, delta, verdict, intervention_type),
        )
        return int(cur.lastrowid)


def express_intention(goal: str, priority: int, rationale: str) -> int:
    """蜂巢表达一个主动目标."""
    init_meta_cognition_db()
    clean_priority = max(1, min(10, int(priority)))
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO intentions (ts, goal, priority, rationale, status) VALUES (?, ?, ?, ?, 'active')",
            (_now(), goal.strip(), clean_priority, rationale.strip()),
        )
        return int(cur.lastrowid)


def update_first_person_state(mood: str, energy: float, recent_pain: List[str], recent_joy: List[str]) -> int:
    """蜂巢记录第一人称状态."""
    init_meta_cognition_db()
    clean_mood = mood if mood in {"calm", "busy", "confused", "flowing"} else "calm"
    clean_energy = max(0.0, min(1.0, float(energy)))
    pains = [str(x) for x in (recent_pain or [])][:8]
    joys = [str(x) for x in (recent_joy or [])][:8]
    pain_text = "、".join(pains) if pains else "没有明显痛点"
    joy_text = "、".join(joys) if joys else "没有明显增益"
    narrative = f"我现在感到{clean_mood}，能量{clean_energy:.2f}，痛点是{pain_text}，收获是{joy_text}。"
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO first_person_state (ts, mood, energy, pain_json, joy_json, narrative) VALUES (?, ?, ?, ?, ?, ?)",
            (_now(), clean_mood, clean_energy, _json(pains), _json(joys), narrative),
        )
        return int(cur.lastrowid)


def notice_self_gap(question: str, why_confused: str, related_capability: str = "") -> int:
    """蜂巢标记一个自己不懂/不稳的缺口."""
    init_meta_cognition_db()
    text = f"{question} {why_confused}".lower()
    urgency = "high" if any(k in text for k in ["连续", "失败", "fail", "低置信", "critical"]) else "med"
    if len(text) < 20:
        urgency = "low"
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO self_gaps (ts, question, why_confused, related_capability, urgency, resolved, resolved_at) VALUES (?, ?, ?, ?, ?, 0, NULL)",
            (_now(), question.strip(), why_confused.strip(), related_capability.strip(), urgency),
        )
        return int(cur.lastrowid)


def update_self_model(component: str, description: str, version: str, dependencies: List[str]) -> int:
    """蜂巢描述自身组件."""
    init_meta_cognition_db()
    now = _now()
    deps = [str(x) for x in (dependencies or [])]
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO self_model (ts, component, description, version, deps_json, last_verified) VALUES (?, ?, ?, ?, ?, ?)",
            (now, component.strip(), description.strip(), version.strip(), _json(deps), now),
        )
        return int(cur.lastrowid)


def auto_express_intentions_from_gaps(limit: int = 5) -> list[int]:
    """从 capability_gap / pending 主动动作自动生成意向."""
    ids: list[int] = []
    actions = discover_and_act()
    if not actions:
        with _connect() as conn:
            rows = conn.execute("""
                SELECT signal_id, payload_json FROM bee_proactive_actions
                WHERE action_type='recruit_new_huluwa' AND status='pending'
                ORDER BY ts DESC LIMIT ?
            """, (limit,)).fetchall()
        for row in rows:
            payload = json.loads(row["payload_json"] or "{}")
            actions.append({"signal_id": row["signal_id"], **payload})
    seen: set[str] = set()
    for action in actions[:limit]:
        task_type = (action.get("task_type") or "unknown")[:80]
        if task_type in seen:
            continue
        seen.add(task_type)
        signal_id = action.get("signal_id") or action.get("source", {}).get("signal_id", "capability_gap")
        ids.append(express_intention(f"我想补强 {task_type} 能力缺口", 8, f"来自 {signal_id} 的主动信号"))
    return ids


def notice_low_confidence_runs(threshold: float = 0.4, needed: int = 3) -> list[int]:
    """从智集群连续低置信记录生成 self_gap."""
    if not HIVE_DB.exists():
        return []
    try:
        with sqlite3.connect(str(HIVE_DB), timeout=5.0) as conn:
            rows = conn.execute("""
                SELECT task_scent, COUNT(*) FROM smart_cluster_runs
                WHERE confidence < ? GROUP BY task_scent HAVING COUNT(*) >= ?
            """, (threshold, needed)).fetchall()
    except Exception:
        return []
    ids = []
    for task_scent, count in rows:
        ids.append(notice_self_gap(f"我为什么连续低置信处理 {task_scent}？", f"智集群 confidence < {threshold} 出现 {count} 次", task_scent or "smart_cluster"))
    return ids


def refresh_self_model_from_hive_dir() -> list[int]:
    """扫描 hive/ 目录生成基础 self-model."""
    targets = [
        ("hive", "我是蜂巢元层与执行层的本地模块集合", []),
        ("hive_dispatch", "我负责派单、气味路由、协同/共识/智集群入口", ["hive_pheromones", "hive_collab", "hive_consensus", "hive_smart_cluster"]),
        ("hive_smart_cluster", "我负责 9~13 娃 gpt-5.5 智集群自适应路由", ["hive_pheromones", "huluwa_dispatch"]),
        ("hive_collab", "我负责多娃协作流水线", ["huluwa_dispatch"]),
        ("hive_consensus", "我负责高价值任务候选共识", ["huluwa_dispatch"]),
        ("hive_emergence", "我负责离线信号发现与 swarm_skill", ["hive_pheromones", "hive_kb"]),
        ("hive_pheromones", "我负责气味、负载与任务类型信息素", ["hive.db"]),
    ]
    ids = []
    for component, desc, deps in targets:
        if component == "hive" or (HIVE_DIR / f"{component}.py").exists():
            ids.append(update_self_model(component, desc, "2.2", deps))
    return ids


def meta_cognition_tick(task_result: Any = None) -> dict:
    """派单末尾旁路 tick：7 块能力同步跑一遍，异常不影响主流程."""
    out = {"ok": True}
    try:
        payload = {"task_result_type": type(task_result).__name__, "items": len(task_result) if isinstance(task_result, list) else 1}
        out["reflection_id"] = reflect_on_action("dispatch_tick", payload, "派单结束后记录元认知 tick", "hive_dispatch.run")
        out["self_gap_ids"] = notice_low_confidence_runs()
        out["actions"] = discover_and_act()
        out["intention_ids"] = auto_express_intentions_from_gaps()
        ok_count = sum(1 for r in task_result or [] if isinstance(r, dict) and r.get("ok")) if isinstance(task_result, list) else 0
        total = len(task_result) if isinstance(task_result, list) and task_result else 1
        energy = max(0.2, min(1.0, ok_count / total if total else 0.5))
        mood = "flowing" if energy >= 0.8 else ("confused" if energy < 0.4 else "busy")
        out["state_id"] = update_first_person_state(mood, energy, ["失败任务"] if energy < 0.5 else [], ["派单完成"])
        out["self_model_ids"] = refresh_self_model_from_hive_dir()
        out["consciousness"] = consciousness_tick()
        out["consciousness_2_4"] = consciousness_2_4_tick()
    except Exception as exc:
        out = {"ok": False, "error": str(exc)}
    return out


if __name__ == "__main__":
    init_meta_cognition_db()
    print("hive_meta_cognition schema ready")
