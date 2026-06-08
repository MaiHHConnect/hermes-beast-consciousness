"""
hive_emergence — 蜂巢 1.16 涌现机制 (离线 daily scan + swarm_skills 路由)

设计稿: 2.0 / 1.16
- 3 类信号发现:
  1. 复现模式 (同 step_pattern 出现 >=3 次)
  2. 强协作 (A→B 链 >=3 次成功)
  3. 能力缺失 (pheromone < 0.3 的 task_type)
- 跨 DB 聚合: collab_steps + consensus_candidates + pheromones + kb
- 路由: 高复现 pattern → 写入 swarm_skills, 后续任务直接复用
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
import json, sqlite3, sys, time
from pathlib import Path
from collections import Counter, defaultdict
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))
import hive_pheromones as hp
import hive_kb as hk
from hive_consciousness_2_3 import apply_forgetting_curve

HIVE_DIR = Path(os.environ.get("HERMES_HOME", "~/.hermes")) / ".hermes/hive"
HIVE_DB = HIVE_DIR / "hive.db"

# 信号发现阈值
PATTERN_REPRODUCE_THRESHOLD = 3  # 同 step_pattern 出现次数
COLLAB_CHAIN_THRESHOLD = 3       # 协作链成功次数
CAPABILITY_GAP_THRESHOLD = 0.30  # pheromone score 低于此视为能力缺失
EMERGENCE_REVIEW_THRESHOLD = 0.6 # pattern 成功率阈值


def init_emergence_db():
    HIVE_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(HIVE_DB), timeout=5.0) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS emergence_signals (
            signal_id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_type TEXT NOT NULL,  -- reproduction | collab_chain | capability_gap
            task_type TEXT, scope TEXT,
            payload_json TEXT NOT NULL,
            score REAL NOT NULL DEFAULT 0.0,
            evidence_count INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'discovered',  -- discovered | reviewed | promoted | dismissed
            review_note TEXT,
            created_at REAL NOT NULL,
            promoted_at REAL
        );
        CREATE INDEX IF NOT EXISTS idx_emergence_signals_type ON emergence_signals(signal_type);
        CREATE INDEX IF NOT EXISTS idx_emergence_signals_status ON emergence_signals(status);
        CREATE INDEX IF NOT EXISTS idx_emergence_signals_score ON emergence_signals(score DESC);

        CREATE TABLE IF NOT EXISTS swarm_skills (
            skill_id INTEGER PRIMARY KEY AUTOINCREMENT,
            skill_name TEXT NOT NULL UNIQUE,
            task_type TEXT NOT NULL,
            pattern_json TEXT NOT NULL,
            step_plan_json TEXT NOT NULL,
            use_count INTEGER NOT NULL DEFAULT 0,
            success_count INTEGER NOT NULL DEFAULT 0,
            last_used_at REAL,
            source_signal_id INTEGER,
            created_at REAL NOT NULL,
            updated_at REAL
        );
        CREATE INDEX IF NOT EXISTS idx_swarm_skills_task_type ON swarm_skills(task_type);
        CREATE INDEX IF NOT EXISTS idx_swarm_skills_use_count ON swarm_skills(use_count DESC);
        """)


# ============ 信号发现 ============

def _detect_reproduction_patterns(lookback_hours: int = 168) -> list[dict]:
    """从 collab_steps 找复现模式 (同 step_pattern 出现 >=3 次)
    复现模式 = (collab_id 内 step_pattern='collector:HID->analyst:HID->verifier:HID') 出现 >=3 次
    """
    cutoff = time.time() - lookback_hours * 3600
    with sqlite3.connect(str(HIVE_DB), timeout=5.0) as conn:
        cur = conn.execute("""
            SELECT c.collab_id, c.task_type, c.status, c.confidence,
                   GROUP_CONCAT(s.role || ':' || s.huluwa_id, '->') AS pattern,
                   AVG(CASE WHEN s.ok=1 THEN 1.0 ELSE 0.0 END) AS step_ok_rate
            FROM collab_tasks c
            JOIN collab_steps s ON s.collab_id = c.collab_id
            WHERE c.created_at >= ?
              AND c.status IN ('success', 'failed')
            GROUP BY c.collab_id
            ORDER BY c.created_at DESC
        """, (cutoff,))
        rows = cur.fetchall()

    # 统计 pattern 出现
    pattern_counter = Counter()
    pattern_meta = {}
    for collab_id, task_type, status, conf, pattern, step_ok_rate in rows:
        if not pattern or '->' not in pattern:
            continue
        # 排除单段失败
        if status != 'success':
            continue
        key = f"{task_type}|{pattern}"
        pattern_counter[key] += 1
        if key not in pattern_meta:
            pattern_meta[key] = {
                "task_type": task_type, "pattern": pattern,
                "ok_count": 0, "total": 0, "confidences": []
            }
        pattern_meta[key]["total"] += 1
        if status == 'success':
            pattern_meta[key]["ok_count"] += 1
        if conf:
            pattern_meta[key]["confidences"].append(conf)

    # 过滤达到阈值
    signals = []
    for key, count in pattern_counter.items():
        if count < PATTERN_REPRODUCE_THRESHOLD:
            continue
        meta = pattern_meta[key]
        success_rate = meta["ok_count"] / max(1, meta["total"])
        avg_conf = sum(meta["confidences"]) / max(1, len(meta["confidences"])) if meta["confidences"] else 0
        if success_rate < EMERGENCE_REVIEW_THRESHOLD:
            continue
        signals.append({
            "signal_type": "reproduction",
            "task_type": meta["task_type"],
            "scope": "collab_pipeline_3",
            "payload": {
                "pattern": meta["pattern"],
                "task_type": meta["task_type"],
                "reproduction_count": count,
                "success_rate": round(success_rate, 3),
                "avg_confidence": round(avg_conf, 3),
            },
            "score": round(success_rate * 0.7 + min(1.0, count / 10.0) * 0.3, 4),
            "evidence_count": count,
        })
    return signals


def _detect_strong_collab(lookback_hours: int = 168) -> list[dict]:
    """从 consensus_runs 找强协作 (winner+少数意见 > 3次)"""
    cutoff = time.time() - lookback_hours * 3600
    with sqlite3.connect(str(HIVE_DB), timeout=5.0) as conn:
        cur = conn.execute("""
            SELECT run_id, task_type, winner_huluwa_id, consensus_score, top1_score, top2_score
            FROM consensus_runs
            WHERE created_at >= ? AND status = 'success'
        """, (cutoff,))
        rows = cur.fetchall()

    # 统计 top1-top2 接近 (<0.08) 多次出现的 pair
    pair_counter = Counter()
    pair_meta = {}
    for run_id, task_type, winner_hid, cons_score, top1, top2 in rows:
        if not winner_hid:
            continue
        key = f"{task_type}|{winner_hid}"
        pair_counter[key] += 1
        if key not in pair_meta:
            pair_meta[key] = {"task_type": task_type, "winner_hid": winner_hid,
                              "scores": [], "count": 0}
        pair_meta[key]["count"] += 1
        if cons_score:
            pair_meta[key]["scores"].append(cons_score)

    signals = []
    for key, count in pair_counter.items():
        if count < COLLAB_CHAIN_THRESHOLD:
            continue
        meta = pair_meta[key]
        avg_score = sum(meta["scores"]) / max(1, len(meta["scores"])) if meta["scores"] else 0
        signals.append({
            "signal_type": "collab_chain",
            "task_type": meta["task_type"],
            "scope": "consensus_winner",
            "payload": {
                "task_type": meta["task_type"],
                "winner_hid": meta["winner_hid"],
                "consensus_count": count,
                "avg_consensus_score": round(avg_score, 3),
            },
            "score": round(avg_score * 0.8 + min(1.0, count / 10.0) * 0.2, 4),
            "evidence_count": count,
        })
    return signals


def _detect_capability_gaps() -> list[dict]:
    """从 pheromones 找能力缺失 (score < 阈值)"""
    with sqlite3.connect(str(HIVE_DB), timeout=5.0) as conn:
        cur = conn.execute("""
            SELECT huluwa_id, task_type, score, success_count, fail_count
            FROM huluwa_task_pheromones
            WHERE (success_count + fail_count) >= 3
            ORDER BY score ASC
            LIMIT 50
        """)
        rows = cur.fetchall()

    signals = []
    for hid, ttype, score, sc, fc in rows:
        uc = sc + fc
        if score >= CAPABILITY_GAP_THRESHOLD:
            continue
        signals.append({
            "signal_type": "capability_gap",
            "task_type": ttype,
            "scope": f"huluwa_{hid}",
            "payload": {
                "huluwa_id": hid, "task_type": ttype,
                "score": round(score, 3), "use_count": uc,
                "success_count": sc, "fail_count": fc,
                "fail_rate": round(fc / max(1, uc), 3),
            },
            "score": round(1.0 - score, 4),
            "evidence_count": uc,
        })
    return signals


# ============ 写入信号 ============

def _save_signals(signals: list[dict]) -> int:
    """写入 emergence_signals (去重: 同 signal_type+task_type+payload 跳过)"""
    if not signals:
        return 0
    n = 0
    with sqlite3.connect(str(HIVE_DB), timeout=5.0) as conn:
        for s in signals:
            # 简单去重
            cur = conn.execute("""
                SELECT signal_id FROM emergence_signals
                WHERE signal_type = ? AND task_type = ? AND scope = ?
                  AND status NOT IN ('dismissed')
                  AND ABS(score - ?) < 0.01
                ORDER BY created_at DESC LIMIT 1
            """, (s["signal_type"], s.get("task_type"), s.get("scope"), s.get("score", 0.0)))
            if cur.fetchone():
                continue
            conn.execute("""
                INSERT INTO emergence_signals
                (signal_type, task_type, scope, payload_json, score, evidence_count, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 'discovered', ?)
            """, (s["signal_type"], s.get("task_type"), s.get("scope"),
                  json.dumps(s["payload"], ensure_ascii=False),
                  s.get("score", 0.0), s.get("evidence_count", 0), time.time()))
            n += 1
        conn.commit()
    return n


# ============ Swarm Skill 路由 ============

def register_swarm_skill(signal_id: int) -> bool:
    """把 signal 注册为 swarm_skill (HIVE-PATCH-1.16)"""
    with sqlite3.connect(str(HIVE_DB), timeout=5.0) as conn:
        cur = conn.execute("""
            SELECT signal_type, task_type, payload_json, score, evidence_count
            FROM emergence_signals WHERE signal_id = ? AND status = 'discovered'
        """, (signal_id,))
        row = cur.fetchone()
        if not row:
            return False
        stype, ttype, payload_str, score, ev_count = row
        payload = json.loads(payload_str)

        # 只有 reproduction 类型能注册为 step_plan swarm_skill
        if stype == "reproduction" and "pattern" in payload:
            pattern = payload["pattern"]
            # pattern: "collector:HID->analyst:HID->verifier:HID"
            step_plan = {}
            for segment in pattern.split("->"):
                role, hid = segment.strip().split(":")
                step_plan[role] = int(hid)
            skill_name = f"collab_{ttype}_{pattern.replace(':', '_').replace('->', '_')[:80]}"
            now = time.time()
            try:
                conn.execute("""
                    INSERT INTO swarm_skills
                    (skill_name, task_type, pattern_json, step_plan_json, use_count, success_count,
                     last_used_at, source_signal_id, created_at, updated_at)
                    VALUES (?, ?, ?, ?, 0, 0, NULL, ?, ?, ?)
                    ON CONFLICT(skill_name) DO UPDATE SET
                        use_count=use_count, success_count=success_count,
                        last_used_at=last_used_at, updated_at=excluded.updated_at
                """, (skill_name, ttype,
                      json.dumps({"pattern": pattern, "score": score, "evidence": ev_count}),
                      json.dumps(step_plan, ensure_ascii=False),
                      signal_id, now, now))
                conn.execute("""
                    UPDATE emergence_signals SET status='promoted', promoted_at=?
                    WHERE signal_id=?
                """, (now, signal_id))
                conn.commit()
                return True
            except Exception as e:
                sys.stderr.write(f"[hive_emergence] register fail: {e}\n")
                return False
        else:
            # collab_chain / capability_gap 只标记 reviewed
            conn.execute("""
                UPDATE emergence_signals SET status='reviewed', review_note=?
                WHERE signal_id=?
            """, (f"non-pattern signal: {stype}", signal_id))
            conn.commit()
            return True
    return False


def route_via_swarm_skill(task_text: str, task_type: str) -> dict | None:
    """如果任务命中已注册的 swarm_skill, 返回 step_plan (给 run_collab forced_plan)"""
    init_emergence_db()
    with sqlite3.connect(str(HIVE_DB), timeout=5.0) as conn:
        cur = conn.execute("""
            SELECT skill_id, skill_name, step_plan_json, use_count, success_count
            FROM swarm_skills
            WHERE task_type = ? OR task_type = 'long_form'
            ORDER BY use_count DESC, created_at DESC
            LIMIT 5
        """, (task_type,))
        rows = cur.fetchall()
    if not rows:
        return None
    # 选 use_count 最高的 (或首次)
    best = rows[0]
    sid, sname, plan_str, uc, sc = best
    try:
        step_plan = json.loads(plan_str)
    except Exception:
        return None
    return {
        "skill_id": sid,
        "skill_name": sname,
        "step_plan": step_plan,
        "use_count": uc,
        "success_count": sc,
    }


def record_swarm_skill_usage(skill_id: int, success: bool) -> None:
    """任务跑完回调, 更新 use_count + success_count"""
    with sqlite3.connect(str(HIVE_DB), timeout=5.0) as conn:
        conn.execute("""
            UPDATE swarm_skills SET
                use_count = use_count + 1,
                success_count = success_count + ?,
                last_used_at = ?
            WHERE skill_id = ?
        """, (1 if success else 0, time.time(), skill_id))
        conn.commit()


# ============ Daily Scan ============

def daily_scan(lookback_hours: int = 168, auto_promote_score: float = 0.85) -> dict:
    """每日扫描, 发现 + 写入信号 + 高分自动 promote"""
    init_emergence_db()
    t0 = time.time()

    # 3 类信号
    repro_signals = _detect_reproduction_patterns(lookback_hours)
    collab_signals = _detect_strong_collab(lookback_hours)
    gap_signals = _detect_capability_gaps()

    all_signals = repro_signals + collab_signals + gap_signals
    saved = _save_signals(all_signals)

    # 高分 auto_promote (reproduction)
    promoted = 0
    if auto_promote_score > 0:
        with sqlite3.connect(str(HIVE_DB), timeout=5.0) as conn:
            cur = conn.execute("""
                SELECT signal_id FROM emergence_signals
                WHERE status='discovered' AND signal_type='reproduction' AND score >= ?
            """, (auto_promote_score,))
            signal_ids = [r[0] for r in cur.fetchall()]
        for sid in signal_ids:
            if register_swarm_skill(sid):
                promoted += 1

    # 扫统计
    with sqlite3.connect(str(HIVE_DB), timeout=5.0) as conn:
        n_signals = conn.execute("SELECT COUNT(*) FROM emergence_signals").fetchone()[0]
        n_promoted = conn.execute("SELECT COUNT(*) FROM emergence_signals WHERE status='promoted'").fetchone()[0]
        n_swarm_skills = conn.execute("SELECT COUNT(*) FROM swarm_skills").fetchone()[0]

    forgetting = apply_forgetting_curve()

    return {
        "duration_ms": int((time.time() - t0) * 1000),
        "signals_detected": len(all_signals),
        "signals_saved": saved,
        "signals_promoted": promoted,
        "reproduction_count": len(repro_signals),
        "collab_chain_count": len(collab_signals),
        "capability_gap_count": len(gap_signals),
        "total_signals": n_signals,
        "total_promoted": n_promoted,
        "total_swarm_skills": n_swarm_skills,
        "auto_promote_score_threshold": auto_promote_score,
        "lookback_hours": lookback_hours,
        "forgetting": forgetting,
    }


def list_signals(status: str | None = None, signal_type: str | None = None) -> list[dict]:
    """列出信号"""
    with sqlite3.connect(str(HIVE_DB), timeout=5.0) as conn:
        q = "SELECT signal_id, signal_type, task_type, scope, payload_json, score, evidence_count, status, created_at, promoted_at FROM emergence_signals"
        args = []
        where = []
        if status:
            where.append("status = ?")
            args.append(status)
        if signal_type:
            where.append("signal_type = ?")
            args.append(signal_type)
        if where:
            q += " WHERE " + " AND ".join(where)
        q += " ORDER BY score DESC, created_at DESC LIMIT 100"
        rows = conn.execute(q, args).fetchall()
    return [{
        "signal_id": r[0], "signal_type": r[1], "task_type": r[2],
        "scope": r[3], "payload": json.loads(r[4]),
        "score": r[5], "evidence_count": r[6], "status": r[7],
        "created_at": r[8], "promoted_at": r[9],
    } for r in rows]


def list_swarm_skills() -> list[dict]:
    with sqlite3.connect(str(HIVE_DB), timeout=5.0) as conn:
        rows = conn.execute("""
            SELECT skill_id, skill_name, task_type, pattern_json, step_plan_json,
                   use_count, success_count, last_used_at, source_signal_id, created_at
            FROM swarm_skills ORDER BY use_count DESC, created_at DESC
        """).fetchall()
    return [{
        "skill_id": r[0], "skill_name": r[1], "task_type": r[2],
        "pattern": json.loads(r[3]) if r[3] else {},
        "step_plan": json.loads(r[4]) if r[4] else {},
        "use_count": r[5], "success_count": r[6],
        "success_rate": round(r[6] / max(1, r[5]), 3) if r[5] else 0,
        "last_used_at": r[7], "source_signal_id": r[8], "created_at": r[9],
    } for r in rows]


if __name__ == "__main__":
    init_emergence_db()
    print("hive_emergence schema ready")
