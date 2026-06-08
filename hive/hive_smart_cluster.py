"""
hive_smart_cluster — 蜂巢 1.14 gpt-5.5 智集群自适应路由

设计: 副后 gpt-5.5 (2026-06-08)
- 9~13 娃智集群内动态选娃 (不碰 1~8 agnes-flash)
- scoring = keyword_match*0.40 + pheromone*0.30 + load*0.15 + type_scent*0.15 + length_bias
- 自适应 fallback 链: top3 + chain_stats 链历史成功率微调
- 置信度衰减: primary self_confidence < 0.4 → 补发 fallback_chain[1] (不全链重跑)
- 集成: 1.16 swarm_skill → 1.14 智集群 → 1.8/1.9 协同共识 → 单娃
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
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, _HERMES_HOME)  # 让 huluwa_dispatch 可被 import
import hive_pheromones as hp
from hive_consciousness_2_3 import maybe_explore
from hive_consciousness_2_4 import apply_value_alignment
from huluwa_dispatch import run_one

HIVE_DIR = Path(os.environ.get("HERMES_HOME", "~/.hermes")) / ".hermes/hive"
HIVE_DB = HIVE_DIR / "hive.db"

SMART_HIDS = [9, 10, 11, 12, 13]
CONFIDENCE_DECAY_THRESHOLD = 0.40

SMART_CLUSTER_META = {
    9:  {"name": "九娃",   "role": "通用推理",  "types": {"general","reasoning","analysis","qa"},
         "keywords": {"推理","分析","判断","解释","方案","策略","为什么","比较","决策","归纳","抽象","逻辑","general"},
         "prompt": "smart_cluster_general"},
    10: {"name": "十娃",   "role": "财经",     "types": {"finance","market","stock","macro","business"},
         "keywords": {"财经","股票","基金","宏观","财报","估值","市场","利率","汇率","债券","期货","投资","营收","利润","资产负债表","现金流","PE","EPS","finance"},
         "prompt": "smart_cluster_finance"},
    11: {"name": "十一娃", "role": "代码",     "types": {"code","coding","debug","programming","dev"},
         "keywords": {"代码","bug","报错","函数","接口","模块","脚本","python","javascript","typescript","sql","api","测试","重构","实现","debug","code"},
         "prompt": "smart_cluster_general"},
    12: {"name": "十二娃", "role": "长文",     "types": {"longform","writing","document","report","summary"},
         "keywords": {"长文","报告","文档","总结","综述","白皮书","大纲","文章","改写","扩写","深度","完整","不少于","万字","longform"},
         "prompt": "smart_cluster_general"},
    13: {"name": "十三娃", "role": "实时信息", "types": {"realtime","search","news","current","fresh"},
         "keywords": {"实时","最新","今天","现在","新闻","搜索","抓取","官网","当前","刚刚","行情","版本","发布","公告","2026","current","realtime"},
         "prompt": "smart_cluster_real_time"},
}

SMART_CLUSTER_PROMPTS = {
    "smart_cluster_real_time": """你是蜂巢 gpt-5.5 智集群的实时信息专员 (十三娃).
要求:
- 先判断是否需要实时来源; 无法联网时明确说明信息可能过期
- 输出简短, 优先给结论+关键依据+时间敏感风险
- 避免超过 90s 输出
- 末尾给 self_confidence (0~1)

任务: {task_text}

JSON: {{"answer":"...","self_confidence":0.0,"notes":"..."}}""",

    "smart_cluster_finance": """你是蜂巢 gpt-5.5 智集群的财经分析专员 (十娃).
要求:
- 先结论, 再 3 条以内核心依据
- 区分事实/假设/观点; 涉及投资必须提示"非投资建议"
- 输出简短, 避免超过 90s
- 末尾给 self_confidence (0~1)

任务: {task_text}

JSON: {{"answer":"...","self_confidence":0.0,"risk":"..."}}""",

    "smart_cluster_general": """你是蜂巢 gpt-5.5 智集群成员 (九/十一/十二娃).
要求:
- 直接完成任务, 不寒暄
- 输出简短但完整; 复杂任务先给结构化要点
- 不做无关扩展, 避免超过 90s
- 末尾给 self_confidence (0~1)

任务: {task_text}

JSON: {{"answer":"...","self_confidence":0.0}}""",
}


def init_smart_cluster_db():
    HIVE_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(HIVE_DB), timeout=5.0) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS smart_cluster_runs (
            run_id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL DEFAULT '',
            task_text TEXT NOT NULL DEFAULT '',
            task_type TEXT NOT NULL DEFAULT '',
            task_scent TEXT NOT NULL DEFAULT '',
            primary_hid INTEGER NOT NULL,
            fallback_chain_json TEXT NOT NULL DEFAULT '[]',
            confidence REAL NOT NULL DEFAULT 0.0,
            reasoning TEXT NOT NULL DEFAULT '',
            success_hid INTEGER,
            success_rate REAL NOT NULL DEFAULT 0.0,
            fallback_used INTEGER NOT NULL DEFAULT 0,
            duration_ms INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL,
            finished_at REAL
        );
        CREATE INDEX IF NOT EXISTS idx_smart_cluster_runs_task_id ON smart_cluster_runs(task_id);
        CREATE INDEX IF NOT EXISTS idx_smart_cluster_runs_primary_hid_created ON smart_cluster_runs(primary_hid, created_at);
        CREATE INDEX IF NOT EXISTS idx_smart_cluster_runs_success_hid_created ON smart_cluster_runs(success_hid, created_at);
        CREATE INDEX IF NOT EXISTS idx_smart_cluster_runs_task_type_created ON smart_cluster_runs(task_type, created_at);
        CREATE INDEX IF NOT EXISTS idx_smart_cluster_runs_scent_created ON smart_cluster_runs(task_scent, created_at);

        CREATE TABLE IF NOT EXISTS smart_cluster_chain_stats (
            chain_json TEXT NOT NULL,
            task_scent TEXT NOT NULL DEFAULT '',
            total_runs INTEGER NOT NULL DEFAULT 0,
            success_runs INTEGER NOT NULL DEFAULT 0,
            success_rate REAL NOT NULL DEFAULT 0.0,
            updated_at REAL NOT NULL,
            PRIMARY KEY(chain_json, task_scent)
        );
        CREATE INDEX IF NOT EXISTS idx_smart_cluster_chain_stats_scent_rate ON smart_cluster_chain_stats(task_scent, success_rate DESC);
        """)


def _clamp01(x):
    return max(0.0, min(1.0, float(x)))


def _normalize_text(t):
    return (t or "").strip()


def infer_task_scent(task_text, task_type=None):
    text = _normalize_text(task_text)
    task_type = (task_type or "").lower()
    if task_type in {"finance","market","stock","macro","business"}: return "finance"
    if task_type in {"realtime","search","news","current","fresh"}: return "realtime"
    if task_type in {"code","coding","debug","programming","dev"}: return "code"
    if task_type in {"longform","writing","document","report","summary"}: return "longform"
    if len(text) >= 1800: return "longform"
    text_l = text.lower()
    for hid, meta in SMART_CLUSTER_META.items():
        if any(k.lower() in text_l for k in meta["keywords"]):
            if hid == 10: return "finance"
            if hid == 11: return "code"
            if hid == 12: return "longform"
            if hid == 13: return "realtime"
    return "general"


def _keyword_match_score(text, hid):
    if not text: return 0.0
    text_l = text.lower()
    keywords = SMART_CLUSTER_META[hid]["keywords"]
    hits = sum(1 for k in keywords if k.lower() in text_l)
    return _clamp01(hits / 3.0)


def _type_scent_score(task_scent, task_type, hid):
    meta_types = SMART_CLUSTER_META[hid]["types"]
    rt = (task_type or "").lower()
    if rt and rt in meta_types: return 1.0
    if task_scent in meta_types: return 1.0
    soft = {("analysis",9),("general",9),("business",10),("debug",11),("document",12),("search",13)}
    if (task_scent, hid) in soft: return 0.7
    return 0.0


def _length_bias(text, hid):
    L = len(_normalize_text(text))
    if hid == 12 and L >= 1800: return 0.08
    if hid == 12 and L >= 900: return 0.04
    if hid == 13 and L >= 1800: return -0.04
    return 0.0


def _get_pheromone_score(hid, task_scent):
    try:
        ph = hp.get_huluwa_task_pheromone(hid, task_scent)
        if ph and "score" in ph:
            return _clamp01(ph["score"])
    except Exception:
        pass
    return 0.5


def _get_load_score(hid):
    try:
        with sqlite3.connect(str(HIVE_DB), timeout=3.0) as conn:
            row = conn.execute("SELECT current_load FROM pheromones WHERE huluwa_id=?", (hid,)).fetchone()
            if row and row[0] is not None:
                busy = _clamp01(float(row[0]))
                return 1.0 - busy
    except Exception:
        pass
    return 0.8


def _get_chain_boost(chain, task_scent):
    if not chain: return 0.0
    try:
        chain_json = json.dumps(chain, ensure_ascii=False)
        with sqlite3.connect(str(HIVE_DB), timeout=3.0) as conn:
            row = conn.execute("SELECT success_rate FROM smart_cluster_chain_stats WHERE chain_json=? AND task_scent=?",
                              (chain_json, task_scent)).fetchone()
            if row:
                return _clamp01(row[0]) * 0.08
    except Exception:
        pass
    return 0.0


def pick_smart_cluster(task_text, task_type=None, *, debug=False):
    """1.14 决策器: 任务 → 智集群 primary + fallback_chain"""
    text = _normalize_text(task_text)
    task_scent = infer_task_scent(text, task_type)
    scores = {}
    for hid in SMART_HIDS:
        kw = _keyword_match_score(text, hid)
        ph = _get_pheromone_score(hid, task_scent)
        ld = _get_load_score(hid)
        tm = _type_scent_score(task_scent, task_type, hid)
        lb = _length_bias(text, hid)
        total = _clamp01(kw * 0.40 + ph * 0.30 + ld * 0.15 + tm * 0.15 + lb)
        scores[hid] = {"keyword_match": round(kw,3), "pheromone_score": round(ph,3),
                       "load": round(ld,3), "type_scent_match": round(tm,3),
                       "length_bias": round(lb,3), "total": round(total,3)}
    ranked = sorted(SMART_HIDS, key=lambda h: (scores[h]["total"], scores[h]["type_scent_match"], scores[h]["keyword_match"]), reverse=True)
    primary = ranked[0]
    candidates = ranked[1:4]
    # 两套链选一个 (链历史成功率 + 加权 sum)
    chain_a = [primary, candidates[0], candidates[1]]
    chain_b = [primary, candidates[1], candidates[0]]
    score_a = _get_chain_boost(chain_a, task_scent) + sum(scores[h]["total"] for h in chain_a) / 100.0
    score_b = _get_chain_boost(chain_b, task_scent) + sum(scores[h]["total"] for h in chain_b) / 100.0
    fallback_chain = chain_a if score_a >= score_b else chain_b
    confidence = scores[primary]["total"]
    meta = SMART_CLUSTER_META[primary]
    reasoning = f"选{primary}{meta['name']}({meta['role']}) - 任务气味={task_scent}, 综合分{confidence:.2f}"
    if debug:
        print(f"  scent={task_scent} primary={primary} chain={fallback_chain} conf={confidence:.2f}")
        for h in SMART_HIDS:
            print(f"    huluwa-{h} {SMART_CLUSTER_META[h]['role']}: {scores[h]}")
    decision = {"primary": primary, "fallback_chain": fallback_chain,
                "confidence": confidence, "reasoning": reasoning, "scores": scores,
                "task_scent": task_scent, "prompt_template": meta["prompt"]}
    decision = maybe_explore(decision, p=0.01)
    return apply_value_alignment(decision)


def should_use_smart_cluster(task_text, task_type=None):
    """1.16 未命中 + 任务值得走智集群 → True"""
    if not task_text: return False
    scent = infer_task_scent(task_text, task_type)
    if scent in {"finance","realtime","code","longform","general"}:
        # long_form/finance_realtime/code 已分别被 1.14/1.15/1.17 接管前, 这里给智集群优先
        return True
    if len(task_text) >= 900: return True
    return False


def _build_smart_prompt(template_name, task_text):
    template = SMART_CLUSTER_PROMPTS.get(template_name, SMART_CLUSTER_PROMPTS["smart_cluster_general"])
    return template.format(task_text=task_text)


def _extract_self_confidence(result):
    if not result: return 0.0
    try:
        text = result.get("content","") or ""
        import re
        m = re.search(r'"self_confidence"\s*:\s*([0-9.]+)', text)
        if m:
            return _clamp01(float(m.group(1)))
        return _clamp01(float(result.get("self_confidence", 0.5)))
    except Exception:
        return 0.5


def _result_quality(result):
    if not result or not result.get("ok"): return 0.0
    text = result.get("content","") or ""
    self_conf = _extract_self_confidence({"content": text})
    completeness = 1.0 if len(text) >= 120 else len(text) / 120.0
    return _clamp01(self_conf * 0.75 + completeness * 0.25)


def update_chain_stats(chain, task_scent, success):
    if not chain: return
    chain_json = json.dumps(chain, ensure_ascii=False)
    now = time.time()
    with sqlite3.connect(str(HIVE_DB), timeout=5.0) as conn:
        cur = conn.execute("""INSERT INTO smart_cluster_chain_stats
            (chain_json, task_scent, total_runs, success_runs, success_rate, updated_at)
            VALUES (?, ?, 1, ?, ?, ?)
            ON CONFLICT(chain_json, task_scent) DO UPDATE SET
                total_runs=total_runs+1,
                success_runs=success_runs+excluded.success_runs,
                success_rate=CAST(success_runs+excluded.success_runs AS REAL)/CAST(total_runs+1 AS REAL),
                updated_at=excluded.updated_at""",
            (chain_json, task_scent, 1 if success else 0, 1.0 if success else 0.0, now))
        conn.commit()


def run_smart_cluster(task_text, task_id=None, task_type=None, timeout=90, debug=False):
    """1.14 智集群自适应: 跑 primary → 必要时补发 fallback_chain[1]"""
    init_smart_cluster_db()
    t0 = time.time()
    decision = pick_smart_cluster(task_text, task_type, debug=debug)
    primary_hid = decision["primary"]
    chain = decision["fallback_chain"]
    task_scent = decision["task_scent"]

    # 写 smart_cluster_runs 占位
    with sqlite3.connect(str(HIVE_DB), timeout=5.0) as conn:
        cur = conn.execute("""INSERT INTO smart_cluster_runs
            (task_id, task_text, task_type, task_scent, primary_hid, fallback_chain_json,
             confidence, reasoning, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (task_id or "", task_text[:8000], task_type or "", task_scent,
             primary_hid, json.dumps(chain, ensure_ascii=False),
             decision["confidence"], decision["reasoning"], t0))
        run_id = cur.lastrowid
        conn.commit()

    # 跑 primary
    primary_prompt = _build_smart_prompt(decision["prompt_template"], task_text)
    primary_result = run_one(primary_hid, primary_prompt, timeout=timeout)
    primary_text = (primary_result.get("content","") if primary_result else "")
    primary_self_conf = _extract_self_confidence({"content": primary_text})
    primary_ok = bool(primary_result and primary_result.get("ok")) and bool(primary_text)

    fallback_used = False
    success_hid = primary_hid
    final_text = primary_text
    final_ok = primary_ok
    if not primary_ok or primary_self_conf < CONFIDENCE_DECAY_THRESHOLD:
        # 触发置信度衰减: 补发 fallback_chain[1]
        fallback_used = True
        fb_hid = chain[1]
        fb_template = SMART_CLUSTER_META[fb_hid]["prompt"]
        fb_prompt = _build_smart_prompt(fb_template, task_text)
        fb_result = run_one(fb_hid, fb_prompt, timeout=timeout)
        fb_text = (fb_result.get("content","") if fb_result else "")
        fb_ok = bool(fb_result and fb_result.get("ok")) and bool(fb_text)
        # 二选一: 取质量分高的
        p_q = _result_quality({"ok": primary_ok, "content": primary_text})
        f_q = _result_quality({"ok": fb_ok, "content": fb_text})
        if f_q > p_q:
            final_text = fb_text
            final_ok = fb_ok
            success_hid = fb_hid

    t1 = time.time()
    total_ms = int((t1 - t0) * 1000)
    success_rate = 1.0 if final_ok else 0.0
    # 更新 run
    with sqlite3.connect(str(HIVE_DB), timeout=5.0) as conn:
        conn.execute("""UPDATE smart_cluster_runs SET
            success_hid=?, success_rate=?, fallback_used=?, duration_ms=?, finished_at=?
            WHERE run_id=?""",
            (success_hid, success_rate, 1 if fallback_used else 0, total_ms, t1, run_id))
        conn.commit()
    # 链统计
    update_chain_stats(chain, task_scent, final_ok)

    # 调权
    try:
        hp.update_huluwa_task_pheromone(success_hid, task_scent, ok=final_ok, root_cause=None)
    except Exception:
        pass
    try:
        from hive_meta_cognition import reflect_on_action, evaluate_intervention
        reflect_on_action(
            "smart_cluster_adjust",
            {"run_id": run_id, "task_scent": task_scent, "success_hid": success_hid, "final_ok": final_ok},
            "智集群结束后记录调权意识",
            "hive_smart_cluster.run_smart_cluster",
        )
        evaluate_intervention(f"smart_cluster:{run_id}", decision["confidence"], success_rate, "smart_cluster_chain")
    except Exception:
        pass

    return {"ok": final_ok, "mode": "smart_cluster_adaptive", "run_id": run_id,
            "task_id": task_id, "task_type": task_type, "task_scent": task_scent,
            "primary": primary_hid, "fallback_chain": chain, "fallback_used": fallback_used,
            "success_hid": success_hid, "confidence": decision["confidence"],
            "reasoning": decision["reasoning"], "content": final_text[:4000],
            "wall_ms": total_ms}


if __name__ == "__main__":
    init_smart_cluster_db()
    print("hive_smart_cluster schema ready")
