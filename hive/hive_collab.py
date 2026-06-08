"""
hive_collab — 蜂巢 1.14 协同任务 (三段式流水线)
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
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))
import hive_kb as hk
import hive_pheromones as hp
from hive_dispatch import classify_task
from huluwa_dispatch import run_one

HIVE_DIR = Path(os.environ.get("HERMES_HOME", "~/.hermes")) / ".hermes/hive"
HIVE_DB = HIVE_DIR / "hive.db"
COLLAB_TIMEOUT_DEFAULT = 180

# HIVE-PATCH-1.14: 三段流水线用混合集群
# - collector/analyst 优先 agnes-flash 快娃 (跑事实+分析, 量大)
# - verifier 优先 gpt-5.5 智集群 (高智能复核, 量少)
ROLE_FALLBACK = {
    "collector": [7, 3, 13, 1, 5],
    "analyst":   [5, 10, 1, 9],
    "verifier":  [9, 12, 8, 11],
}


def init_collab_db():
    HIVE_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(HIVE_DB), timeout=5.0) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS collab_tasks (
            collab_id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT, task_text TEXT NOT NULL, task_type TEXT NOT NULL,
            mode TEXT NOT NULL DEFAULT 'pipeline_3',
            status TEXT NOT NULL DEFAULT 'pending',
            final_result TEXT, confidence REAL NOT NULL DEFAULT 0.0,
            failed_role TEXT, fail_reason TEXT,
            collector_huluwa_id INTEGER, analyst_huluwa_id INTEGER, verifier_huluwa_id INTEGER,
            total_duration_ms INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL, started_at REAL, finished_at REAL
        );
        CREATE INDEX IF NOT EXISTS idx_collab_tasks_task_type ON collab_tasks(task_type);
        CREATE INDEX IF NOT EXISTS idx_collab_tasks_status ON collab_tasks(status);
        CREATE INDEX IF NOT EXISTS idx_collab_tasks_created_at ON collab_tasks(created_at);
        CREATE TABLE IF NOT EXISTS collab_steps (
            step_id INTEGER PRIMARY KEY AUTOINCREMENT,
            collab_id INTEGER NOT NULL, role TEXT NOT NULL, step_order INTEGER NOT NULL,
            huluwa_id INTEGER NOT NULL,
            match_method TEXT NOT NULL DEFAULT 'unknown',
            match_score REAL NOT NULL DEFAULT 0.0,
            input_json TEXT NOT NULL, output_json TEXT, raw_output TEXT,
            ok INTEGER NOT NULL DEFAULT 0, fail_reason TEXT,
            duration_ms INTEGER NOT NULL DEFAULT 0,
            started_at REAL NOT NULL, finished_at REAL,
            UNIQUE (collab_id, role)
        );
        CREATE INDEX IF NOT EXISTS idx_collab_steps_collab_id ON collab_steps(collab_id);
        CREATE INDEX IF NOT EXISTS idx_collab_steps_huluwa_id ON collab_steps(huluwa_id);
        CREATE INDEX IF NOT EXISTS idx_collab_steps_pattern ON collab_steps(collab_id, step_order, role, huluwa_id);
        """)


def should_run_collab(task_text, task_type):
    text = (task_text or "").lower()
    if task_type in {"finance_realtime", "research", "code_review"}:
        return True
    if task_type == "long_form" and len(text) > 500:
        return True
    if len(text) > 500 and task_type in {"general", "code"}:
        return True
    return False


def _build_step_prompt(role, task_text, task_type, collector_out=None, analyst_out=None):
    if role == "collector":
        return f"""你是蜂巢协同流水线 collector 阶段。

任务类型: {task_type}
原始任务: {task_text[:800]}

职责: 只收集事实/证据/来源/限制,不做最终判断; 不确定写 unknown; 只输出 JSON。
Schema: role="collector", ok=true, facts=[{{claim,source,confidence}}], evidence=[{{title,detail,source,relevance}}], source_limits=[], open_questions=[], summary="100字内"。"""
    if role == "analyst":
        cj = json.dumps(collector_out, ensure_ascii=False) if collector_out else "{}"
        task_clip = task_text[:800]
        cj_clip = cj[:1200]
        prompt = f"""你是蜂巢协同 analyst。

类型: {task_type}
任务: {task_clip}
collector: {cj_clip}

职责: 仅据 collector 分析,不编造; 给结论/推理/建议/风险; 只输出 JSON。
Schema: role,ok,conclusion,reasoning[point,based_on,confidence],actions[action,priority],risks[risk,severity],confidence,answer_draft。"""
        if len(prompt) > 1500:
            over = len(prompt) - 1500
            cj_clip = cj_clip[:max(0, len(cj_clip) - over)]
            prompt = f"""你是蜂巢协同 analyst。

类型: {task_type}
任务: {task_clip}
collector: {cj_clip}

职责: 仅据 collector 分析,不编造; 给结论/推理/建议/风险; 只输出 JSON。
Schema: role,ok,conclusion,reasoning[point,based_on,confidence],actions[action,priority],risks[risk,severity],confidence,answer_draft。"""
        return prompt
    if role == "verifier":
        cj = json.dumps(collector_out, ensure_ascii=False) if collector_out else "{}"
        aj = json.dumps(analyst_out, ensure_ascii=False) if analyst_out else "{}"
        task_clip = task_text[:800]
        cj_clip = cj[:800]
        aj_clip = aj[:800]
        prompt = f"""你是蜂巢协同 verifier。

类型: {task_type}
任务: {task_clip}
collector: {cj_clip}
analyst: {aj_clip}

职责: 查 analyst 是否越证据,找错漏,给最终答案; 只输出 JSON。
Schema: role,ok,errors[type,detail,severity],missing,confidence,final_answer,safe_to_return。"""
        if len(prompt) > 1500:
            over = len(prompt) - 1500
            cut_cj = min(len(cj_clip), (over + 1) // 2)
            cut_aj = over - cut_cj
            cj_clip = cj_clip[:max(0, len(cj_clip) - cut_cj)]
            aj_clip = aj_clip[:max(0, len(aj_clip) - cut_aj)]
            prompt = f"""你是蜂巢协同 verifier。

类型: {task_type}
任务: {task_clip}
collector: {cj_clip}
analyst: {aj_clip}

职责: 查 analyst 是否越证据,找错漏,给最终答案; 只输出 JSON。
Schema: role,ok,errors[type,detail,severity],missing,confidence,final_answer,safe_to_return。"""
        return prompt
    raise ValueError(f"unknown role: {role}")


def _parse_step_json(role, raw):
    if not raw: return None
    text = raw.strip()
    try: return json.loads(text)
    except: pass
    s, e = text.find("{"), text.rfind("}")
    if s >= 0 and e > s:
        try: return json.loads(text[s:e+1])
        except: pass
    return None


def _run_collab_step(role, step_order, huluwa_id, task_text, task_type,
                     collector_out, analyst_out, collab_id, used_huluwas, timeout):
    t0 = time.time()
    try:
        hid, score, method = hp.pick_by_pheromone_v17(
            task_text=task_text, task_type=task_type, exclude=used_huluwas
        )
    except Exception as e:
        hid, score, method = None, 0.0, f"pheromone_v17_error:{e}"

    if hid is None:
        for fb in ROLE_FALLBACK.get(role, []):
            if fb not in used_huluwas:
                hid, score, method = fb, 0.0, f"collab_fallback:{role}"
                break
    if hid is None:
        for x in range(1, 14):
            if x not in used_huluwas:
                hid, score, method = x, 0.0, "collab_last_resort"
                break
    if hid is None:
        return {"role": role, "step_order": step_order, "huluwa_id": 0,
                "match_method": "no_huluwa", "match_score": 0.0,
                "ok": False, "fail_reason": "no_available_huluwa",
                "duration_ms": 0, "started_at": t0, "finished_at": time.time(),
                "output_json": None, "raw_output": ""}
    used_huluwas.add(hid)

    prompt = _build_step_prompt(role, task_text, task_type, collector_out, analyst_out)
    input_json = json.dumps({"role": role, "task_type": task_type}, ensure_ascii=False)

    with sqlite3.connect(str(HIVE_DB), timeout=5.0) as conn:
        conn.execute("""INSERT INTO collab_steps
            (collab_id, role, step_order, huluwa_id, match_method, match_score, input_json, started_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (collab_id, role, step_order, hid, method, score, input_json, t0))
        conn.commit()

    try:
        r = run_one(hid, prompt, timeout=timeout)
        raw_output = r.get("content", "")
        wall_ms = r.get("wall_ms", 0)
        ok = bool(r.get("ok")) and bool(raw_output)
    except Exception as e:
        raw_output = f"exc:{type(e).__name__}:{e}"
        wall_ms = int((time.time() - t0) * 1000)
        ok = False

    parsed = _parse_step_json(role, raw_output) if ok else None
    parsed_ok = bool(parsed and parsed.get("ok", True))
    final_ok = ok and parsed_ok
    fail_reason = "" if final_ok else (
        "json_parse_error" if ok and not parsed else f"run_fail:{raw_output[:200]}"
    )

    t1 = time.time()
    with sqlite3.connect(str(HIVE_DB), timeout=5.0) as conn:
        conn.execute("""UPDATE collab_steps SET
            output_json=?, raw_output=?, ok=?, fail_reason=?,
            duration_ms=?, finished_at=?
            WHERE collab_id=? AND role=?""",
            (json.dumps(parsed, ensure_ascii=False) if parsed else None,
             raw_output[:4000], 1 if final_ok else 0, fail_reason[:500],
             wall_ms, t1, collab_id, role))
        conn.commit()

    return {"role": role, "step_order": step_order, "huluwa_id": hid,
            "match_method": method, "match_score": score,
            "ok": final_ok, "fail_reason": fail_reason,
            "duration_ms": wall_ms, "started_at": t0, "finished_at": t1,
            "output_json": parsed, "raw_output": raw_output[:4000]}


def run_collab(task_text, task_id=None, timeout=COLLAB_TIMEOUT_DEFAULT, forced_plan=None):
    """跑三段式协同任务 (collector→analyst→verifier)
    forced_plan: 1.16 swarm_skill 复用, {role: huluwa_id}
    """
    init_collab_db()
    t0 = time.time()

    task_type = classify_task(task_text)
    if len(task_text) > 500 and task_type in {"general", "code"}:
        task_type = "long_form"
    if not should_run_collab(task_text, task_type):
        return {"ok": False, "mode": "not_collab", "reason": "not_collab_task_type", "task_type": task_type}

    with sqlite3.connect(str(HIVE_DB), timeout=5.0) as conn:
        cur = conn.execute("""INSERT INTO collab_tasks
            (task_id, task_text, task_type, status, created_at, started_at)
            VALUES (?, ?, ?, 'running', ?, ?)""",
            (task_id, task_text[:8000], task_type, t0, t0))
        collab_id = cur.lastrowid
        conn.commit()

    used_huluwas = set()
    steps = []
    prev_collector = None
    prev_analyst = None
    failed_role = None

    for role, step_order in [("collector", 1), ("analyst", 2), ("verifier", 3)]:
        if forced_plan and role in forced_plan:
            forced_hid = forced_plan[role]
            used_huluwas.add(forced_hid)
            t0_step = time.time()
            if role == "collector":
                prompt = _build_step_prompt(role, task_text, task_type, None, None)
            elif role == "analyst":
                prompt = _build_step_prompt(role, task_text, task_type, prev_collector, None)
            else:
                prompt = _build_step_prompt(role, task_text, task_type, prev_collector, prev_analyst)
            r = run_one(forced_hid, prompt, timeout=timeout)
            raw = r.get("content", "")
            ok_run = bool(r.get("ok")) and bool(raw)
            parsed = _parse_step_json(role, raw) if ok_run else None
            final_ok = ok_run and bool(parsed and parsed.get("ok", True))
            t1_step = time.time()
            with sqlite3.connect(str(HIVE_DB), timeout=5.0) as conn:
                conn.execute("""INSERT INTO collab_steps
                    (collab_id, role, step_order, huluwa_id, match_method, match_score,
                     input_json, output_json, raw_output, ok, fail_reason, duration_ms, started_at, finished_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(collab_id, role) DO UPDATE SET
                        output_json=excluded.output_json, raw_output=excluded.raw_output,
                        ok=excluded.ok, fail_reason=excluded.fail_reason,
                        duration_ms=excluded.duration_ms, finished_at=excluded.finished_at""",
                    (collab_id, role, step_order, forced_hid, "forced_plan", 0.0,
                     json.dumps({"role": role}, ensure_ascii=False),
                     json.dumps(parsed, ensure_ascii=False) if parsed else None,
                     raw[:4000], 1 if final_ok else 0,
                     "" if final_ok else ("json_parse_error" if ok_run and not parsed else f"run_fail:{raw[:200]}"),
                     r.get("wall_ms", 0), t0_step, t1_step))
                conn.commit()
            step_result = {"role": role, "step_order": step_order, "huluwa_id": forced_hid,
                "match_method": "forced_plan", "match_score": 0.0,
                "ok": final_ok, "fail_reason": "" if final_ok else "forced_fail",
                "duration_ms": r.get("wall_ms", 0), "started_at": t0_step, "finished_at": t1_step,
                "output_json": parsed, "raw_output": raw[:4000]}
        else:
            step_result = _run_collab_step(role, step_order, None, task_text, task_type,
                                           prev_collector, prev_analyst, collab_id, used_huluwas, timeout)

        steps.append(step_result)
        if not step_result["ok"]:
            failed_role = role
            break
        if role == "collector":
            prev_collector = step_result["output_json"]
        elif role == "analyst":
            prev_analyst = step_result["output_json"]

    t1 = time.time()
    total_ms = int((t1 - t0) * 1000)
    if failed_role:
        with sqlite3.connect(str(HIVE_DB), timeout=5.0) as conn:
            conn.execute("""UPDATE collab_tasks SET
                status='failed', failed_role=?, fail_reason=?,
                total_duration_ms=?, finished_at=? WHERE collab_id=?""",
                (failed_role, steps[-1].get("fail_reason", "")[:500], total_ms, t1, collab_id))
            conn.commit()
        for s in steps:
            if s["role"] == failed_role:
                hp.update_huluwa_task_pheromone(
                    huluwa_id=s["huluwa_id"], task_type=task_type, ok=False, root_cause="run")
        return {"ok": False, "mode": "collab_pipeline_3", "collab_id": collab_id,
                "task_id": task_id, "task_type": task_type, "confidence": 0.0,
                "failed_role": failed_role, "fail_reason": steps[-1].get("fail_reason", ""),
                "steps": steps, "total_duration_ms": total_ms}

    verifier_out = steps[-1].get("output_json") or {}
    confidence = float(verifier_out.get("confidence", 0.0) or 0.0)
    confidence = max(0.0, min(1.0, confidence))
    final_answer = verifier_out.get("final_answer", "")
    safe = verifier_out.get("safe_to_return", True)
    if not safe:
        confidence = min(confidence, 0.5)

    with sqlite3.connect(str(HIVE_DB), timeout=5.0) as conn:
        conn.execute("""UPDATE collab_tasks SET
            status='success', final_result=?, confidence=?,
            collector_huluwa_id=?, analyst_huluwa_id=?, verifier_huluwa_id=?,
            total_duration_ms=?, finished_at=? WHERE collab_id=?""",
            (json.dumps({"final_answer": final_answer, "verifier_output": verifier_out}, ensure_ascii=False)[:8000],
             confidence, steps[0]["huluwa_id"], steps[1]["huluwa_id"], steps[2]["huluwa_id"],
             total_ms, t1, collab_id))
        conn.commit()

    for s in steps:
        hp.update_huluwa_task_pheromone(
            huluwa_id=s["huluwa_id"], task_type=task_type, ok=True, root_cause=None)

    try:
        hk.add(task_type=task_type, task_text=task_text,
            huluwa_id=steps[-1]["huluwa_id"],
            experience=f"协同链路 {len(steps)} 娃成功: " + ",".join(s['role']+':'+str(s['huluwa_id']) for s in steps),
            tools_used=["collab_pipeline_3"],
            duration_ms=total_ms, success=1, fail_reason=None,
            experience_dict={"problem": task_text[:300], "outcome_reason": "三段式协同成功",
                "action": "step_pattern=" + "->".join(s['role']+':'+str(s['huluwa_id']) for s in steps),
                "anti_pattern": "", "validation": f"confidence={confidence:.2f}",
                "scope": task_type, "root_cause": "unknown", "stage": "collab_finalize", "confidence": confidence})
    except Exception as e:
        sys.stderr.write(f"[hive_collab] hk.add fail: {e}\n")

    return {"ok": True, "mode": "collab_pipeline_3", "collab_id": collab_id,
            "task_id": task_id, "task_type": task_type,
            "final_result": final_answer, "confidence": confidence,
            "failed_role": None, "steps": steps, "total_duration_ms": total_ms}


if __name__ == "__main__":
    init_collab_db()
    print("hive_collab schema ready")
