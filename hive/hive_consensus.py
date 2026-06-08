"""
hive_consensus — 蜂巢 1.15 共识机制 (3 候选 + 蚁后裁决)

设计稿: 1.9 / 1.15
- 高价值任务: 3 娃独立答题
- 评分公式: 0.30 factual + 0.25 completeness + 0.20 actionability + 0.15 history_weight + 0.10 self_confidence - risk_penalty
- top1-top2 < 0.08 触发复核
- 蚁后 LLM (llm gpt-5.5) 合并 final_answer
- 少数意见保留
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

sys.path.insert(0, str(Path(__file__).parent))
import hive_kb as hk
import hive_pheromones as hp
from hive_dispatch import classify_task
from huluwa_dispatch import run_one
from concurrent.futures import ThreadPoolExecutor, as_completed

HIVE_DIR = Path(os.environ.get("HERMES_HOME", "~/.hermes")) / ".hermes/hive"
HIVE_DB = HIVE_DIR / "hive.db"
CONSENSUS_TIMEOUT = 120

CONSENSUS_FALLBACK_POOL = {
    "finance_realtime": [10, 13, 5],
    "code":             [11, 2, 9],
    "code_review":      [11, 2, 9],
    "research":         [13, 7, 9],
    "long_form":        [12, 9, 5],
    "general":          [9, 8, 12],
}


def init_consensus_db():
    HIVE_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(HIVE_DB), timeout=5.0) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS consensus_runs (
            run_id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT, task_text TEXT NOT NULL, task_type TEXT NOT NULL,
            candidate_count INTEGER NOT NULL DEFAULT 3,
            status TEXT NOT NULL DEFAULT 'pending',
            winner_id INTEGER,
            winner_huluwa_id INTEGER,
            final_answer TEXT, consensus_score REAL NOT NULL DEFAULT 0.0,
            top1_score REAL NOT NULL DEFAULT 0.0, top2_score REAL NOT NULL DEFAULT 0.0,
            review_triggered INTEGER NOT NULL DEFAULT 0,
            review_huluwa_id INTEGER, review_output TEXT,
            dissent_summary TEXT, judge_prompt TEXT, judge_raw_output TEXT,
            fail_reason TEXT,
            total_duration_ms INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL, started_at REAL, finished_at REAL
        );
        CREATE INDEX IF NOT EXISTS idx_consensus_runs_task_type ON consensus_runs(task_type);
        CREATE INDEX IF NOT EXISTS idx_consensus_runs_status ON consensus_runs(status);
        CREATE INDEX IF NOT EXISTS idx_consensus_runs_created_at ON consensus_runs(created_at);
        CREATE INDEX IF NOT EXISTS idx_consensus_runs_task_id ON consensus_runs(task_id);

        CREATE TABLE IF NOT EXISTS consensus_candidates (
            candidate_id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            huluwa_id INTEGER NOT NULL,
            match_method TEXT NOT NULL DEFAULT 'unknown',
            match_score REAL NOT NULL DEFAULT 0.0,
            answer TEXT NOT NULL, raw_output TEXT,
            self_confidence REAL NOT NULL DEFAULT 0.5,
            factual_score REAL NOT NULL DEFAULT 0.5,
            completeness_score REAL NOT NULL DEFAULT 0.5,
            actionability_score REAL NOT NULL DEFAULT 0.5,
            risk_score REAL NOT NULL DEFAULT 0.0,
            risk_penalty REAL NOT NULL DEFAULT 0.0,
            history_weight REAL NOT NULL DEFAULT 0.5,
            final_score REAL NOT NULL DEFAULT 0.0,
            selected INTEGER NOT NULL DEFAULT 0,
            minority_opinion INTEGER NOT NULL DEFAULT 0,
            ok INTEGER NOT NULL DEFAULT 0,
            fail_reason TEXT,
            duration_ms INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL,
            UNIQUE (run_id, huluwa_id)
        );
        CREATE INDEX IF NOT EXISTS idx_consensus_candidates_run_id ON consensus_candidates(run_id);
        CREATE INDEX IF NOT EXISTS idx_consensus_candidates_huluwa_id ON consensus_candidates(huluwa_id);
        CREATE INDEX IF NOT EXISTS idx_consensus_candidates_score ON consensus_candidates(run_id, final_score DESC);
        CREATE INDEX IF NOT EXISTS idx_consensus_candidates_selected ON consensus_candidates(selected);
        """)


def should_run_consensus(task_text, task_type):
    """高价值任务触发"""
    text = (task_text or "").lower()
    if task_type == "finance_realtime":
        return any(k in text for k in ["判断", "预测", "买", "卖", "风险", "投资", "走势", "仓位", "追涨", "适合"])
    if task_type in {"code", "code_review"}:
        return any(k in text for k in ["方案", "架构", "重构", "安全", "性能", "选型", "review", "评审"])
    if task_type == "research":
        return any(k in text for k in ["争议", "对比", "判断", "结论", "是否", "应该"])
    if task_type == "long_form" and len(text) > 500:
        return True
    return False


# ============ 评分公式 ============

def _score_candidate(candidate, task_type):
    """评分 (heuristic-based)"""
    answer_text = candidate.get("answer_text", "")
    text_lower = answer_text.lower()
    self_conf = float(candidate.get("self_confidence", 0.5) or 0.5)
    self_conf = max(0.0, min(1.0, self_conf))

    # factual: 提到 evidence/source 关键词 + 没保证/必涨等强词
    factual = 0.5
    if any(k in answer_text for k in ["证据", "evidence", "来源", "source", "数据", "时间"]):
        factual += 0.2
    if any(k in answer_text for k in ["不确定", "unknown", "无法确认", "限制", "暂无", "时间戳"]):
        factual += 0.1
    if any(k in answer_text for k in ["保证", "必涨", "稳赚", "绝对", "无风险"]):
        factual -= 0.3
    factual = max(0.0, min(1.0, factual))

    # completeness: 答案长度 + 关键字段都有
    completeness = min(1.0, len(answer_text) / 500.0)
    if len(answer_text) > 1000:
        completeness = min(1.0, 0.7 + (len(answer_text) - 1000) / 5000.0)

    # actionability: 提到 actions/suggest/建议/步骤
    actionability = 0.3
    if any(k in answer_text for k in ["建议", "action", "步骤", "step", "执行", "操作"]):
        actionability += 0.3
    if any(k in answer_text for k in ["如果", "前提", "验证", "止损", "测试", "条件"]):
        actionability += 0.2
    actionability = max(0.0, min(1.0, actionability))

    # risk: 出现保证/必涨/无风险 + 没提风险
    risk = 0.0
    if any(k in answer_text for k in ["保证", "必然", "稳赚", "无风险"]):
        risk += 0.7
    if "风险" not in answer_text and "不确定" not in answer_text:
        risk += 0.3
    if len(answer_text) < 200:
        risk += 0.2
    risk = max(0.0, min(1.0, risk))
    risk_penalty = min(0.30, risk * 0.30)

    # history_weight: 从 task_type pheromone 拿
    huluwa_id = candidate.get("huluwa_id")
    history_weight = 0.5
    phero = hp.get_huluwa_task_pheromone(huluwa_id, task_type)
    if phero:
        history_weight = float(phero.get("score", 0.5) or 0.5)
    history_weight = max(0.0, min(1.0, history_weight))

    final_score = (
        0.30 * factual
        + 0.25 * completeness
        + 0.20 * actionability
        + 0.15 * history_weight
        + 0.10 * self_conf
        - risk_penalty
    )
    final_score = max(0.0, min(1.0, final_score))

    return {
        "factual_score": round(factual, 4),
        "completeness_score": round(completeness, 4),
        "actionability_score": round(actionability, 4),
        "risk_score": round(risk, 4),
        "risk_penalty": round(risk_penalty, 4),
        "history_weight": round(history_weight, 4),
        "final_score": round(final_score, 4),
        "self_confidence": self_conf,
    }


# ============ 候选 prompt ============

def _build_candidate_prompt(task_text, task_type):
    return f"""你是蜂巢共识机制的独立候选回答者。

任务类型: {task_type}
原始任务: {task_text[:1500]}

要求:
1. 独立作答,不要假设其他候选答案
2. 明确事实依据、推理过程、可执行建议和风险
3. 不确定时必须说不确定
4. 最后给出 self_confidence (0~1)
5. 输出必须是 JSON, 不要 Markdown

请严格输出:
{{"answer":"你的完整答案","key_claims":["关键判断"],"evidence":["依据"],"actions":["可执行建议"],"risks":["风险和限制"],"self_confidence":0.5}}"""


def _parse_candidate_json(raw):
    if not raw: return None
    text = raw.strip()
    try: return json.loads(text)
    except: pass
    s, e = text.find("{"), text.rfind("}")
    if s >= 0 and e > s:
        try: return json.loads(text[s:e+1])
        except: pass
    return None


# ============ 蚁后 LLM 裁决 ============

def _llm_chat(prompt, system="你是JSON答题器,只输出JSON.", model="gpt-5.5", temperature=0.5, timeout=90):
    """直接调 llm API (不经 hermes CLI) - 3-10s 出结果"""
    import os, urllib.request
    api_key = os.environ.get("LLM_API_KEY", "")
    if not api_key:
        for line in open(os.path.join(os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes")), ".hermes/.env")).read().splitlines():
            line = line.strip()
            if line.startswith("LLM_"):
                k, v = line.split("=", 1)
                api_key = v.strip().strip(chr(34)).strip(chr(39))
                os.environ[k] = api_key
                break
    if not api_key:
        return {"ok": False, "content": "", "fail_reason": "no_api_key"}
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt}
        ],
        "temperature": temperature,
    }
    req = urllib.request.Request(
        "<LLM_BASE_URL>/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
    )
    try:
        t0 = time.time()
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            out = data["choices"][0]["message"]["content"]
        return {"ok": True, "content": out, "wall_ms": int((time.time()-t0)*1000)}
    except Exception as e:
        return {"ok": False, "content": f"exc:{type(e).__name__}:{e}", "fail_reason": str(e)[:200]}


def _queen_judge(task_text, task_type, candidates, score_matrix, dissent_summary, timeout=60):
    """蚁后 LLM 合并 final_answer (调 llm gpt-5.5)"""
    import os, urllib.request
    api_key = os.environ.get("LLM_API_KEY", "")
    base_url = "<LLM_BASE_URL>"
    if not api_key:
        return {"ok": False, "fail_reason": "no_api_key", "final_answer": "", "consensus_score": 0.0}

    # 构造 prompt
    candidates_summary = []
    for c in candidates[:3]:
        candidates_summary.append({
            "candidate_id": c["candidate_id"],
            "huluwa_id": c["huluwa_id"],
            "answer_excerpt": c.get("answer", "")[:800],
            "final_score": c["final_score"],
            "self_confidence": c.get("self_confidence", 0.5),
        })
    prompt = f"""你是蜂巢蚁后裁决器。

任务类型: {task_type}
原始任务: {task_text[:1500]}

候选答案 (前 800 字摘要):
{json.dumps(candidates_summary, ensure_ascii=False, indent=2)}

评分矩阵:
{json.dumps(score_matrix, ensure_ascii=False, indent=2)}

少数意见摘要: {dissent_summary[:500]}

指令:
1. 综合候选答案,不要机械选 top1
2. 保留少数意见指出的关键风险
3. 输出 final_answer (给用户的答案) + winner_candidate_id (选哪个候选)
4. 严格 JSON 输出,不要 Markdown

{{"winner_candidate_id":0,"final_answer":"最终答案","consensus_score":0.0,"risk_notes":["仍需提示的风险"],"used_fragments":[{{"candidate_id":0,"fragment":"采用片段"}}]}}"""

    # 调 gpt-5.5
    body = {
        "model": "gpt-5.5",
        "messages": [
            {"role": "system", "content": "你是蜂巢蚁后裁决器,只输出 JSON."},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.3,
    }
    req = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            raw_output = data["choices"][0]["message"]["content"]
        parsed = _parse_candidate_json(raw_output)
        if parsed:
            return {
                "ok": True,
                "winner_candidate_id": int(parsed.get("winner_candidate_id", 0)),
                "final_answer": parsed.get("final_answer", ""),
                "consensus_score": float(parsed.get("consensus_score", 0.0)),
                "risk_notes": parsed.get("risk_notes", []),
                "judge_raw": raw_output,
            }
        return {"ok": False, "fail_reason": "json_parse", "judge_raw": raw_output,
                "final_answer": "", "consensus_score": 0.0}
    except Exception as e:
        return {"ok": False, "fail_reason": f"queen_llm:{type(e).__name__}:{e}",
                "judge_raw": "", "final_answer": "", "consensus_score": 0.0}


# ============ 选 3 娃 ============

def _pick_consensus_huluwas(task_text, task_type, n=3):
    """选 n 个不同娃 ID (优先 pick_by_pheromone_v17, fallback 池补齐)"""
    picked = []
    used = set()
    # 先用 pheromone_v17 选 1 个
    try:
        hid, score, method = hp.pick_by_pheromone_v17(task_text, task_type, exclude=set())
        if hid is not None and hid not in used:
            picked.append({"huluwa_id": hid, "score": score, "method": method})
            used.add(hid)
    except Exception:
        pass
    # 用 fallback 池补齐
    fb_pool = CONSENSUS_FALLBACK_POOL.get(task_type, [9, 8, 12])
    for fb_hid in fb_pool:
        if len(picked) >= n: break
        if fb_hid not in used:
            picked.append({"huluwa_id": fb_hid, "score": 0.0, "method": "consensus_fallback"})
            used.add(fb_hid)
    # 还不够从全池补
    for x in range(1, 14):
        if len(picked) >= n: break
        if x not in used:
            picked.append({"huluwa_id": x, "score": 0.0, "method": "consensus_backfill"})
            used.add(x)
    return picked[:n]


# ============ 主函数 ============

def run_consensus(task_text, task_id=None, timeout=CONSENSUS_TIMEOUT, concurrency=3):
    init_consensus_db()
    t0 = time.time()
    task_type = classify_task(task_text)
    if not should_run_consensus(task_text, task_type):
        return {"ok": False, "mode": "not_consensus", "reason": "not_high_value",
                "task_type": task_type}

    # 写 consensus_runs
    with sqlite3.connect(str(HIVE_DB), timeout=5.0) as conn:
        cur = conn.execute("""INSERT INTO consensus_runs
            (task_id, task_text, task_type, status, created_at, started_at)
            VALUES (?, ?, ?, 'running', ?, ?)""", (task_id, task_text[:8000], task_type, t0, t0))
        run_id = cur.lastrowid
        conn.commit()

    # 选 3 娃
    picked = _pick_consensus_huluwas(task_text, task_type, n=3)
    if len(set(p["huluwa_id"] for p in picked)) < 3:
        with sqlite3.connect(str(HIVE_DB), timeout=5.0) as conn:
            conn.execute("""UPDATE consensus_runs SET status='failed', fail_reason='less_than_3_huluwas',
                finished_at=? WHERE run_id=?""", (time.time(), run_id))
            conn.commit()
        return {"ok": False, "mode": "consensus_3", "run_id": run_id,
                "fail_reason": "less_than_3_huluwas"}

    # 并发 3 候选 (HIVE-PATCH-1.15-LLM: 直接 llm API, 不走 hermes CLI)
    # 3 候选用不同 system prompt: 保守派 / 激进派 / 中立派
    base_prompt = _build_candidate_prompt(task_text, task_type)
    cand_system_prompts = [
        "你是蜂巢共识机制候选 A (保守派). 注重风险控制, 不确定时宁可不建议, 输出 JSON.",
        "你是蜂巢共识机制候选 B (激进派). 注重机会捕捉, 给出具体行动建议, 输出 JSON.",
        "你是蜂巢共识机制候选 C (中立派). 综合分析, 平衡风险和机会, 输出 JSON.",
    ]
    cand_results = []
    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        def _run_llm_candidate(idx, picked_item, system_prompt):
            # HIVE-PATCH: 用 llm API 调, 保留 huluwa_id 作为元数据
            r = _llm_chat(prompt=base_prompt, system=system_prompt, model="gpt-5.5", timeout=timeout)
            return picked_item, r
        futs = {ex.submit(_run_llm_candidate, i, p, cand_system_prompts[i % 3]): p for i, p in enumerate(picked)}
        for fut in as_completed(futs):
            p = futs[fut]
            try:
                _, r = fut.result()
                raw = r.get("content", "")
                ok_run = bool(r.get("ok")) and bool(raw)
            except Exception as e:
                raw = f"exc:{type(e).__name__}:{e}"
                ok_run = False
                r = {"wall_ms": 0}
            parsed = _parse_candidate_json(raw) if ok_run else None
            # HIVE-PATCH-1.15-容错: 即使 JSON 解析失败, 也用 raw_output 作为 answer
            # (避免 hermes 截断导致 json_parse 失败 -> 整个候选被判废)
            if ok_run:
                if parsed:
                    ans_text = (parsed or {}).get("answer", raw[:1500])
                    conf = float((parsed or {}).get("self_confidence", 0.5) or 0.5)
                    cand_ok = True
                    cand_fail = ""
                else:
                    # 截断或格式异常 - 用 raw_output 整段作为 answer
                    ans_text = raw[:1500] if raw else ""
                    conf = 0.5  # 默认 self_confidence
                    cand_ok = True  # 容错: 仍算 ok
                    cand_fail = "json_parse_fallback_raw"
            else:
                ans_text = ""
                conf = 0.0
                cand_ok = False
                cand_fail = "run_fail"
            cand_results.append({
                "candidate_id": None,  # SQLite 写库时再赋值
                "huluwa_id": p["huluwa_id"],
                "match_method": p["method"],
                "match_score": p["score"],
                "raw_output": raw[:4000],
                "answer": ans_text,
                "answer_text": ans_text,
                "self_confidence": conf,
                "ok": cand_ok,
                "fail_reason": cand_fail,
                "duration_ms": r.get("wall_ms", 0),
                "parsed": parsed,
            })

    # 评分
    scored = []
    for c in cand_results:
        scores = _score_candidate(c, task_type)
        scored.append({**c, **scores})

    # 排序
    scored.sort(key=lambda x: x["final_score"], reverse=True)
    top1 = scored[0] if scored else None
    top2 = scored[1] if len(scored) > 1 else None

    ok_cands = [c for c in scored if c["ok"]]
    if len(ok_cands) < 2:
        with sqlite3.connect(str(HIVE_DB), timeout=5.0) as conn:
            conn.execute("""UPDATE consensus_runs SET status='failed', fail_reason='less_than_2_valid_candidates',
                total_duration_ms=?, finished_at=? WHERE run_id=?""",
                (int((time.time()-t0)*1000), time.time(), run_id))
            conn.commit()
        return {"ok": False, "mode": "consensus_3", "run_id": run_id,
                "fail_reason": "less_than_2_valid_candidates", "candidates": scored}

    # top1-top2 < 0.08 触发复核
    review_triggered = False
    review_huluwa_id = None
    review_output = None
    if top1 and top2 and (top1["final_score"] - top2["final_score"] < 0.08):
        review_triggered = True
        used = {c["huluwa_id"] for c in scored}
        rv_id = None
        for x in [9, 12, 11, 8]:
            if x not in used:
                rv_id = x; break
        if rv_id is None:
            rv_id = 8
        review_huluwa_id = rv_id
        review_prompt = f"""你是蜂巢共识复核 verifier。top1 和 top2 分差 < 0.08。

原始任务: {task_text[:800]}

候选 A (top1 score={top1["final_score"]}): {top1["answer"][:600]}
候选 B (top2 score={top2["final_score"]}): {top2["answer"][:600]}

判断:
1. 哪个更可信 (preferred=A/B/merge)
2. 关键风险

JSON: {{"preferred":"A","critical_risks":[],"reason":""}}"""
        try:
            r = run_one(rv_id, review_prompt, timeout)
            review_output = r.get("content", "")[:1500]
        except Exception as e:
            review_output = f"review_fail:{e}"

    # 少数意见 (HIVE-PATCH-1.15-修: 用 huluwa_id 比较, candidate_id 在写库前为 None)
    dissent = []
    winner_so_far = top1
    winner_hid = winner_so_far.get("huluwa_id") if winner_so_far else None
    for c in scored:
        if c.get("huluwa_id") == winner_hid:
            continue
        if c.get("risk_score", 0) >= 0.4 and "风险" in c.get("answer", ""):
            dissent.append({"huluwa_id": c["huluwa_id"], "opinion": c["answer"][:300],
                            "reason": "high_risk_value"})
    dissent_summary = json.dumps(dissent, ensure_ascii=False)[:1500]

    # 评分矩阵
    score_matrix = [{
        "candidate_id": c["candidate_id"], "huluwa_id": c["huluwa_id"],
        "final_score": c["final_score"], "factual": c["factual_score"],
        "completeness": c["completeness_score"], "actionability": c["actionability_score"],
        "history_weight": c["history_weight"], "self_confidence": c["self_confidence"],
        "risk_penalty": c["risk_penalty"]
    } for c in scored]

    # 蚁后 LLM 裁决
    judge = _queen_judge(task_text, task_type, scored, score_matrix, dissent_summary, timeout=60)
    if judge.get("ok") and judge.get("final_answer"):
        final_answer = judge["final_answer"]
        consensus_score = float(judge.get("consensus_score", top1["final_score"] * 0.95))
        winner_cid = int(judge.get("winner_candidate_id", 0))
    else:
        # fallback: top1
        final_answer = top1["answer"]
        consensus_score = top1["final_score"] * 0.9
        winner_cid = top1.get("candidate_id", 0)

    consensus_score = max(0.0, min(1.0, consensus_score))

    # 找 winner huluwa_id
    winner_huluwa_id = top1["huluwa_id"]
    for c in scored:
        if c.get("candidate_id") == winner_cid:
            winner_huluwa_id = c["huluwa_id"]
            break

    # 写 consensus_runs
    t1 = time.time()
    total_ms = int((t1 - t0) * 1000)
    with sqlite3.connect(str(HIVE_DB), timeout=5.0) as conn:
        conn.execute("""UPDATE consensus_runs SET
            status='success', winner_id=?, winner_huluwa_id=?,
            final_answer=?, consensus_score=?,
            top1_score=?, top2_score=?,
            review_triggered=?, review_huluwa_id=?, review_output=?,
            dissent_summary=?, judge_raw_output=?,
            total_duration_ms=?, finished_at=?
            WHERE run_id=?""",
            (winner_cid, winner_huluwa_id,
             final_answer[:4000], consensus_score,
             top1["final_score"] if top1 else 0.0,
             top2["final_score"] if top2 else 0.0,
             1 if review_triggered else 0, review_huluwa_id,
             review_output[:1500] if review_output else "",
             dissent_summary,
             judge.get("judge_raw", "")[:4000],
             total_ms, t1, run_id))
        conn.commit()

    # 写 consensus_candidates
    with sqlite3.connect(str(HIVE_DB), timeout=5.0) as conn:
        for c in scored:
            cid = c["candidate_id"] if "candidate_id" in c else None
            selected = 1 if (cid is not None and cid == winner_cid) else 0
            minority = 1 if any(d["huluwa_id"] == c["huluwa_id"] for d in dissent) else 0
            conn.execute("""INSERT INTO consensus_candidates
                (run_id, huluwa_id, match_method, match_score, answer, raw_output,
                 self_confidence, factual_score, completeness_score, actionability_score,
                 risk_score, risk_penalty, history_weight, final_score,
                 selected, minority_opinion, ok, fail_reason, duration_ms, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, huluwa_id) DO UPDATE SET
                    answer=excluded.answer, raw_output=excluded.raw_output,
                    final_score=excluded.final_score, selected=excluded.selected""",
                (run_id, c["huluwa_id"], c.get("match_method", "unknown"),
                 c.get("match_score", 0.0), c["answer"][:4000], c["raw_output"],
                 c["self_confidence"], c["factual_score"], c["completeness_score"],
                 c["actionability_score"], c["risk_score"], c["risk_penalty"],
                 c["history_weight"], c["final_score"],
                 selected, minority, 1 if c["ok"] else 0,
                 c.get("fail_reason", "")[:300], c.get("duration_ms", 0), t0))
        conn.commit()

    # 调权
    for c in scored:
        if c.get("candidate_id") == winner_cid:
            hp.update_huluwa_task_pheromone(c["huluwa_id"], task_type, ok=True, root_cause=None)
        elif c.get("final_score", 0) < 0.45 or c.get("risk_penalty", 0) > 0.20:
            hp.update_huluwa_task_pheromone(c["huluwa_id"], task_type, ok=False, root_cause="run")

    return {
        "ok": True, "mode": "consensus_3", "run_id": run_id,
        "task_id": task_id, "task_type": task_type,
        "winner_huluwa_id": winner_huluwa_id, "winner_candidate_id": winner_cid,
        "final_answer": final_answer, "consensus_score": consensus_score,
        "top1_score": top1["final_score"] if top1 else 0.0,
        "top2_score": top2["final_score"] if top2 else 0.0,
        "review_triggered": review_triggered,
        "dissent_summary": dissent_summary,
        "candidates": scored, "total_duration_ms": total_ms,
    }


if __name__ == "__main__":
    init_consensus_db()
    print("hive_consensus schema ready")
