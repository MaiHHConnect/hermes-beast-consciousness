#!/usr/bin/env python3
"""
hive_dispatch — 蜂巢派单器（带气味路由）

替代/包装 huluwa_dispatch.py，加气味匹配层：
1. 任务描述 -> embedding（Qwen3-Embedding-8B via SiliconFlow）
2. 算 task vs 8 娃 scent embedding 的余弦相似度
3. 排除 cooldown
4. 选 top 1
5. fallback 到 round-robin（如果匹配度低或 embedding 失败）
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


import argparse
import json
import os
import subprocess
import sys
import time
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# 把 huluwa_dispatch 当库用
sys.path.insert(0, _HERMES_HOME)
from huluwa_dispatch import run_one, HULUWA_NAMES

HIVE_DIR = Path(os.environ.get("HERMES_HOME", "~/.hermes")) / ".hermes/hive"
sys.path.insert(0, str(HIVE_DIR))
from hive_pheromones import (
    HIVE_DB, init_db, record_task, list_pheromones,
    scent_hash, get_cached_scent_embedding, set_cached_scent_embedding,
    increment_load, pick_by_pheromone_v17, update_huluwa_task_pheromone,
)
import hive_kb as hk

HERMES = os.path.join(os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes")), ".hermes/hermes-agent/venv/bin/hermes")
HOME = "/Users/mac"


def get_embedding(text: str):
    """获取文本 embedding，调 SiliconFlow API"""
    import urllib.request

    api_key = os.environ.get("SILICONFLOW_API_KEY", "")
    if not api_key:
        return None

    url = "https://api.siliconflow.cn/v1/embeddings"
    payload = {
        "model": "Qwen/Qwen3-Embedding-8B",
        "input": text[:2000],
        "encoding_format": "float"
    }

    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode(),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            }
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            return data["data"][0]["embedding"]
    except Exception as e:
        sys.stderr.write(f"[hive] embedding fail: {e}\n")
        return None


def cosine(a, b):
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)




# HIVE-PATCH-1.2: 任务类型分类与候选池
def classify_task(text: str) -> str:
    """按关键词识别任务类型，返回 finance_realtime / code / translate / research / general"""
    text = (text or "").lower()
    keyword_rules = [
        ("finance_realtime", ["股票", "财经", "资金", "板块", "a股", "大盘", "行情"]),
        ("code", ["代码", "python", "bug", "修复", "编程"]),
        ("translate", ["翻译", "英文", "localization"]),
        ("research", ["研究", "搜索", "调研", "情报"]),
    ]
    for task_type, keywords in keyword_rules:
        if any(keyword in text for keyword in keywords):
            return task_type
    return "general"


TASK_CANDIDATES = {
    "finance_realtime": [7, 5, 3],   # 7娃 搜索研究 / 5娃 数据分析 / 3娃 数据采集
    "code": [2],                       # 2娃
    "translate": [4],                  # 4娃
    "research": [7, 5],                # 7娃 / 5娃
    "general": [8, 1, 6],              # 8娃 兜底 / 1娃 / 6娃
}


def augment_task_prompt(task_text: str, max_iterations: int = 3, max_tool_calls: int = 4, max_wall_time: int = 60) -> str:
    """在任务末尾追加蜂巢协作注记（防大娃撞墙，1.2.1 修反效果）

    HIVE-PATCH-1.2.1: 旧版"必须立即停止并输出当前最优结果"被 LLM 解读为
    "主动跑满 8 轮拿最优"，反而导致 4/6 任务 timeout。改友好文案：快答优先。
    """
    return (
        f"{task_text.rstrip()}\n\n"
        "蜂巢协作注记：\n"
        f"- 你的预算：最多 {max_iterations} 轮思考 + {max_tool_calls} 次工具调用 + {max_wall_time}s 墙钟。\n"
        "- **直接给答案优先**：3 轮思考足够。\n"
        "- **不要反复抓数据**：如果不知道就说不知道，不要硬编。\n"
        "- **快速答 > 完美答**：给一个合理答案比超时 0 答案好。"
    )


_classify_rr_state = {}


def classify_then_pick(task_text: str, exclude: set = None) -> tuple:
    """low_margin 时按任务类型从候选池 round-robin 选娃"""
    exclude = exclude or set()
    task_type = classify_task(task_text)
    candidates = TASK_CANDIDATES.get(task_type, TASK_CANDIDATES["general"])
    available = [c for c in candidates if c not in exclude]
    if not available:
        available = [c for c in TASK_CANDIDATES["general"] if c not in exclude]
    if not available:
        return None, 0.0, "classify_round_robin_none"
    idx = _classify_rr_state.get(task_type, 0)
    hid = available[idx % len(available)]
    _classify_rr_state[task_type] = idx + 1
    return hid, 0.60, f"classify_round_robin:{task_type}"


# 蜂巢硬限额常量
# HIVE-PATCH-1.2.1: 反效果修复
# 旧版 8/10/120 逼 LLM 主动跑满 8 轮（"立即停止"被解读为"完成 8 轮拿最优"）
# 改 3/4/60 + 友好文案（"快速答" 而非 "完成 8 轮"）
HIVE_MAX_ITERATIONS = 3
HIVE_MAX_TOOL_CALLS = 4
HIVE_MAX_WALL_TIME = 60


_SCENT_EMBEDDINGS = {}


def load_scent_embeddings():
    global _SCENT_EMBEDDINGS
    rows = list_pheromones()
    for r in rows:
        scent_text = r['scent_text'] or r['name']
        scent_hash_value = scent_hash(scent_text)
        emb = get_cached_scent_embedding(r['huluwa_id'], scent_hash_value)
        if emb is None:
            emb = get_embedding(scent_text)
            set_cached_scent_embedding(r['huluwa_id'], emb, scent_hash_value)
        _SCENT_EMBEDDINGS[r['huluwa_id']] = {
            'name': r['name'],
            'scent_text': scent_text,
            'embedding': emb,
        }
    return _SCENT_EMBEDDINGS


def pick_by_pheromone(task_text, exclude=None):
    exclude = exclude or set()
    task_emb = get_embedding(task_text)
    if not task_emb:
        return None, 0.0, "no_embedding"

    scores = []
    for hid, info in _SCENT_EMBEDDINGS.items():
        if hid in exclude:
            continue
        emb = info.get('embedding')
        if not emb:
            continue
        s = cosine(task_emb, emb)
        scores.append((hid, s))

    if not scores:
        return None, 0.0, "no_scent"

    scores.sort(key=lambda x: -x[1])
    top_id, top_score = scores[0]
    top2_score = scores[1][1] if len(scores) > 1 else 0.0

    if top_score < 0.3:
        return None, top_score, "low_confidence"
    # HIVE-PATCH: top1-top2 分差太小视为不稳，交给 fallback 并标记 low_margin
    if top_score - top2_score < 0.05:
        return None, top_score, "low_margin"

    return top_id, top_score, "pheromone"


class HiveDispatch:
    def __init__(self, concurrency=3, timeout=300, pool_size=13):
        self.concurrency = min(concurrency, pool_size)
        self.timeout = timeout
        self.pool_size = pool_size
        self.stats = {"ok": 0, "fail": 0, "total_ms": 0,
                      "pheromone": 0, "fallback": 0}
        self._rr_idx = 0

        if not HIVE_DB.exists():
            init_db()
        else:
            # HIVE-PATCH-1.13: HULUWA_SCENTS 加新娃时强制 sync 到 pheromones 表
            init_db()
        load_scent_embeddings()

    def _pick_round_robin(self, exclude=None):
        exclude = exclude or set()
        for _ in range(self.pool_size):
            hid = (self._rr_idx % self.pool_size) + 1
            self._rr_idx += 1
            if hid not in exclude:
                return hid
        return ((self._rr_idx - 1) % self.pool_size) + 1

    def _pick(self, task_text, exclude=None):
        # HIVE-PATCH-1.13: 移除短任务 round_robin 短路，否则 gpt-5.5 集群 (9~13) 永远不会被选中
        # 旧版 len(task_text) < 30 强制 round_robin → 短任务全落 1~8 娃
        # 新版所有任务都走 pheromone_v17
        t_type = classify_task(task_text)
        try:
            hid, score, method = pick_by_pheromone_v17(task_text, t_type, exclude)
        except Exception as e:
            sys.stderr.write(f"[hive] pheromone_v17 fail: {e}\n")
            hid, score, method = None, 0.0, "pheromone_v17_error"
        if hid is None:
            hid, score, method = pick_by_pheromone(task_text, exclude)
        if hid is None:
            # HIVE-PATCH-1.2: fallback 不再 round-robin 随机，按任务类型选候选池
            hid, score, method = classify_then_pick(task_text, exclude)
            if hid is None:
                hid = self._pick_round_robin(exclude)
                method = "round_robin_fallback"
        return hid, score, method

    def run(self, tasks):
        results = [None] * len(tasks)
        # HIVE-PATCH-1.16: 涌现 swarm_skill 路由 (2.0 离线 daily scan 写入)
        # 命中条件: route_via_swarm_skill() 返回 step_plan → 强制 run_collab 用 forced_plan
        from hive_emergence import route_via_swarm_skill, record_swarm_skill_usage
        from hive_collab import should_run_collab, run_collab as _run_collab
        swarm_routed = {}
        for idx, t in enumerate(tasks):
            try:
                t_type_check = classify_task(t['task'])
            except Exception:
                t_type_check = "general"
            if should_run_collab(t['task'], t_type_check):
                # 1.16 涌现: 先看有没有现成的 step_plan
                ss = route_via_swarm_skill(t['task'], t_type_check)
                forced = ss["step_plan"] if ss else None
                collab_r = _run_collab(
                    task_text=t['task'],
                    task_id=t.get('id', str(idx)),
                    timeout=self.timeout,
                    forced_plan=forced
                )
                # 1.16: 回调 update use_count/success_count
                if ss:
                    record_swarm_skill_usage(ss["skill_id"], collab_r.get('ok', False))
                results[idx] = {
                    "id": t.get('id', str(idx)),
                    "task": t['task'],
                    "huluwa": (collab_r.get('steps', [{}])[-1].get('huluwa_id', 0) if collab_r.get('steps') else 0),
                    "name": "蜂巢协同" + (" (swarm_skill)" if ss else ""),
                    "ok": collab_r.get('ok', False),
                    "content": (collab_r.get('final_result', '') or collab_r.get('fail_reason', ''))[:4000],
                    "wall_ms": collab_r.get('total_duration_ms', 0),
                    "match_method": "swarm_skill_" + ss["skill_name"] if ss else "collab_pipeline_3",
                    "match_score": collab_r.get('confidence', 0.0),
                    "collab_id": collab_r.get('collab_id'),
                    "failed_role": collab_r.get('failed_role'),
                    "swarm_skill_id": ss["skill_id"] if ss else None,
                }
                continue
        # HIVE-PATCH-1.16-END
        # HIVE-PATCH-1.14: gpt-5.5 智集群自适应路由 (9~13 娃动态选娃 + 置信度衰减)
        # 命中条件: 1.16 未命中 + 任务值得走智集群 (general/finance/realtime/longform/code 或长任务)
        # 不和 1.15 共识冲突: 1.15 走高价值判断任务, 1.14 走通用智集群任务
        # 1.14 不碰 1~8 agnes-flash: 只在 9~13 智集群内选
        from hive_smart_cluster import should_use_smart_cluster, run_smart_cluster as _run_smart_cluster
        for idx, t in enumerate(tasks):
            if results[idx] is not None:
                continue
            try:
                t_type_check = classify_task(t['task'])
            except Exception:
                t_type_check = "general"
            if should_use_smart_cluster(t['task'], t_type_check):
                smart_r = _run_smart_cluster(
                    task_text=t['task'],
                    task_id=t.get('id', str(idx)),
                    task_type=t_type_check,
                    timeout=self.timeout,
                )
                results[idx] = {
                    "id": t.get('id', str(idx)),
                    "task": t['task'],
                    "huluwa": smart_r.get('success_hid', 0),
                    "name": "智集群自适应" + (" (fallback)" if smart_r.get('fallback_used') else ""),
                    "ok": smart_r.get('ok', False),
                    "content": (smart_r.get('content', '') or '')[:4000],
                    "wall_ms": smart_r.get('wall_ms', 0),
                    "match_method": "smart_cluster_adaptive",
                    "match_score": smart_r.get('confidence', 0.0),
                    "smart_cluster_run_id": smart_r.get('run_id'),
                    "smart_cluster_primary": smart_r.get('primary'),
                    "smart_cluster_chain": smart_r.get('fallback_chain'),
                    "smart_cluster_scent": smart_r.get('task_scent'),
                }
                continue
        # HIVE-PATCH-1.14-END
        # HIVE-PATCH-1.15: 共识机制入口 (1.9 3 候选 + 蚁后裁决)
                collab_r = _run_collab(
                    task_text=t['task'],
                    task_id=t.get('id', str(idx)),
                    timeout=self.timeout
                )
                results[idx] = {
                    "id": t.get('id', str(idx)),
                    "task": t['task'],
                    "huluwa": (collab_r.get('steps', [{}])[-1].get('huluwa_id', 0) if collab_r.get('steps') else 0),
                    "name": "蜂巢协同",
                    "ok": collab_r.get('ok', False),
                    "content": (collab_r.get('final_result', '') or collab_r.get('fail_reason', ''))[:4000],
                    "wall_ms": collab_r.get('total_duration_ms', 0),
                    "match_method": "collab_pipeline_3",
                    "match_score": collab_r.get('confidence', 0.0),
                    "collab_id": collab_r.get('collab_id'),
                    "failed_role": collab_r.get('failed_role'),
                }
                continue
        # HIVE-PATCH-1.15: 共识机制入口 (1.9 3 候选 + 蚁后 LLM 裁决)
        # 命中条件: should_run_consensus() → 高价值任务 (含判断/预测/买/卖/方案/重构等关键词)
        from hive_consensus import should_run_consensus, run_consensus as _run_consensus
        for idx, t in enumerate(tasks):
            if results[idx] is not None:
                continue
            try:
                t_type_check = classify_task(t['task'])
            except Exception:
                t_type_check = "general"
            if should_run_consensus(t['task'], t_type_check):
                cons_r = _run_consensus(
                    task_text=t['task'],
                    task_id=t.get('id', str(idx)),
                    timeout=self.timeout
                )
                results[idx] = {
                    "id": t.get('id', str(idx)),
                    "task": t['task'],
                    "huluwa": cons_r.get('winner_huluwa_id', 0),
                    "name": "蜂巢共识",
                    "ok": cons_r.get('ok', False),
                    "content": (cons_r.get('final_answer', '') or cons_r.get('fail_reason', ''))[:4000],
                    "wall_ms": cons_r.get('total_duration_ms', 0),
                    "match_method": "consensus_3",
                    "match_score": cons_r.get('consensus_score', 0.0),
                    "consensus_run_id": cons_r.get('run_id'),
                    "review_triggered": cons_r.get('review_triggered', False),
                    "top1_score": cons_r.get('top1_score', 0.0),
                    "top2_score": cons_r.get('top2_score', 0.0),
                }
                continue
        with ThreadPoolExecutor(max_workers=self.concurrency) as ex:
            futs = {}
            for idx, t in enumerate(tasks):
                if results[idx] is not None:
                    continue
                hid, score, method = self._pick(t['task'])
                increment_load(hid)
                # HIVE-PATCH-1.2: 任务 prompt 增强，注入硬限额
                augmented_task = augment_task_prompt(
                    t['task'],
                    max_iterations=HIVE_MAX_ITERATIONS,
                    max_tool_calls=HIVE_MAX_TOOL_CALLS,
                    max_wall_time=HIVE_MAX_WALL_TIME,
                )
                # HIVE-PATCH-1.3.1: 派单前 add 占位 + query 群体经验注入
                # 解决异步时序错乱: add 在派单时（同步），update 在跑完后
                t_type = classify_task(t['task'])
                placeholder_id = hk.add(
                    task_type=t_type,
                    task_text=t['task'],
                    huluwa_id=hid,
                    experience="_pending_",  # 占位非空串（add 校验）跑完 update 覆盖
                    tools_used=None,
                    duration_ms=None,
                    success=0,  # 暂存为 0
                    fail_reason="",  # 占位空串
                )
                memories = hk.query(t['task'], task_type=t_type, top_k=3)
                # 过滤掉自己占位
                memories = [m for m in memories if int(m['id']) != placeholder_id]
                inject_count = len(memories)
                inject_ids = [int(m['id']) for m in memories] if memories else []
                sys.stderr.write(f"[hive] {t.get('id', '?')}: type={t_type} query_recalled={inject_count} ids={inject_ids} placeholder={placeholder_id}\n")
                sys.stderr.flush()
                if memories:
                    augmented_task = augmented_task + "\n\n" + hk.format_injection(memories)
                    # touch 引用计数
                    for m in memories:
                        hk.touch(int(m['id']))
                fut = ex.submit(run_one, hid, augmented_task, self.timeout)
                futs[fut] = (idx, t, hid, score, method, placeholder_id)

            for fut in as_completed(futs):
                idx, t, hid, score, method, placeholder_id = futs[fut]
                r = fut.result()
                r['id'] = t.get('id', str(idx))
                r['task'] = t['task']
                r['match_method'] = method
                r['match_score'] = score
                results[idx] = r

                try:
                    record_task(hid, r['id'], t['task'], r['ok'], r['wall_ms'],
                                match_method=method, match_score=score)
                except Exception as e:
                    sys.stderr.write(f"[hive] record fail: {e}\n")

                # HIVE-PATCH-1.4: 跑完同步抽取 5 段结构化经验 + 失败归因，再 update 占位
                experience_dict = None
                root_cause = "unknown"
                try:
                    content = r.get('content') or t['task']
                    fail_reason = None if r.get('ok') else content[:300]
                    tools_used = r.get('tools_used')
                    if isinstance(tools_used, str):
                        tools_for_extract = [tools_used]
                    elif tools_used is None:
                        tools_for_extract = []
                    else:
                        tools_for_extract = list(tools_used)
                    experience_dict = hk.extract_experience(
                        sub_hermes_output=content,
                        task_text=t['task'],
                        ok=bool(r.get('ok')),
                        fail_reason=fail_reason,
                        tools_used=tools_for_extract,
                    )
                    root_cause = str((experience_dict or {}).get('root_cause') or 'unknown')
                    rendered_experience = hk._render_fts_text(experience_dict, content[:300])
                    hk.update_memory(
                        mem_id=placeholder_id,
                        experience=rendered_experience[:2000],
                        success=bool(r.get('ok')),
                        fail_reason=fail_reason,
                        duration_ms=r.get('wall_ms'),
                        tools_used=tools_used,
                        experience_dict=experience_dict,
                    )
                except Exception as e:
                    sys.stderr.write(f"[hive] kb.update fail: {e}\n")

                try:
                    update_huluwa_task_pheromone(
                        huluwa_id=hid,
                        task_type=classify_task(t['task']),
                        ok=bool(r.get('ok')),
                        root_cause=root_cause,
                    )
                except Exception as e:
                    sys.stderr.write(f"[hive] task pheromone update fail: {e}\n")

                if r['ok']:
                    self.stats['ok'] += 1
                    if method == 'pheromone':
                        self.stats['pheromone'] += 1
                    else:
                        self.stats['fallback'] += 1
                else:
                    self.stats['fail'] += 1
                self.stats['total_ms'] += r['wall_ms']

                preview = r['content'][:60].replace('\n', ' ')
                sys.stderr.write(
                    f"[{idx+1}/{len(tasks)}] huluwa-{r['huluwa']} ({r['name']}) "
                    f"{method}={score:.2f} {r['wall_ms']}ms ok={r['ok']} -> {preview}\n"
                )
                sys.stderr.flush()
        try:
            from hive_meta_cognition import meta_cognition_tick
            meta_cognition_tick(results)
        except Exception as e:
            sys.stderr.write(f"[hive] meta cognition tick fail: {e}\n")
        return results


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--concurrency', '-c', type=int, default=3)
    p.add_argument('--timeout', '-t', type=int, default=300)
    p.add_argument('--input', '-i')
    p.add_argument('--output', '-o')
    p.add_argument('--task')
    p.add_argument('--repeat', type=int, default=1)
    args = p.parse_args()

    if args.task:
        tasks = [{"id": str(i+1), "task": args.task} for i in range(args.repeat)]
    elif args.input:
        tasks = []
        with open(args.input) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                j = json.loads(line)
                tasks.append({"id": str(j.get("id", len(tasks)+1)), "task": j["task"]})
    else:
        print("need --task or --input")
        sys.exit(1)

    d = HiveDispatch(concurrency=args.concurrency, timeout=args.timeout)
    print(f"[hive] {len(tasks)} tasks, concurrency={args.concurrency}, "
          f"loaded {len(_SCENT_EMBEDDINGS)} scents", file=sys.stderr)
    results = d.run(tasks)

    if args.output:
        with open(args.output, 'w') as f:
            for r in results:
                f.write(json.dumps(r, ensure_ascii=False) + '\n')
        print(f"\n[hive] wrote {args.output}", file=sys.stderr)
    else:
        print()
        for r in results:
            print(f"--- {r['id']} (huluwa-{r['huluwa']} {r['name']}, "
                  f"{r['match_method']}={r['match_score']:.2f}, {r['wall_ms']}ms) ---")
            print(r['content'])

    print(f"\n[stats] ok={d.stats['ok']} fail={d.stats['fail']} "
          f"pheromone={d.stats['pheromone']} fallback={d.stats['fallback']} "
          f"avg_ms={d.stats['total_ms']//len(tasks)}", file=sys.stderr)


if __name__ == "__main__":
    main()
