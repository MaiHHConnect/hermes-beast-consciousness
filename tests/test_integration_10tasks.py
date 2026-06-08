#!/usr/bin/env python3
"""蜂巢混合路由集成测试: 1.16 → 1.14 → 1.15 → 单娃。"""
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

import sys
from pathlib import Path
from unittest.mock import patch

HIVE_DIR = Path(os.environ.get("HERMES_HOME", "~/.hermes")) / ".hermes/hive"
HERMES_DIR = Path(_HERMES_HOME or os.path.expanduser("~/\1"))
sys.path.insert(0, str(HIVE_DIR))
sys.path.insert(0, str(HERMES_DIR))

import hive_dispatch
from hive_dispatch import HiveDispatch

TASKS = [
    {
        "id": "swarm-long-form",
        "task": "写一份 2026 蜂巢架构完整报告，覆盖 1.0 到 1.16，每章要有复盘和落地步骤。" * 8,
        "expected_method": "swarm_skill_",
        "expected_huluwa": 12,
    },
    {
        "id": "swarm-research-report",
        "task": "整理蜂巢协同链路复现报告，要求 collector analyst verifier 三段证据链完整。" * 8,
        "expected_method": "swarm_skill_",
        "expected_huluwa": 9,
    },
    {
        "id": "swarm-code-review",
        "task": "生成蜂巢代码评审长报告，覆盖风险、修复建议、测试计划和回归矩阵。" * 8,
        "expected_method": "swarm_skill_",
        "expected_huluwa": 11,
    },
    {
        "id": "smart-finance",
        "task": "分析茅台最新季报，给出营收利润、现金流、估值和风险摘要。",
        "expected_method": "smart_cluster_adaptive",
        "expected_huluwa": 10,
    },
    {
        "id": "smart-realtime",
        "task": "查询 OpenAI GPT-5.5 最新公告，并总结发布时间、功能变化和限制。",
        "expected_method": "smart_cluster_adaptive",
        "expected_huluwa": 13,
    },
    {
        "id": "smart-code",
        "task": "代码: 修一个 python bug，定位 NoneType 报错并给最小补丁。",
        "expected_method": "smart_cluster_adaptive",
        "expected_huluwa": 11,
    },
    {
        "id": "consensus-stock-buy",
        "task": "判断是否买入腾讯股票，必须比较买入、观望、卖出三种方案。",
        "expected_method": "consensus_3",
        "expected_huluwa": 10,
    },
    {
        "id": "consensus-architecture",
        "task": "判断这个核心服务是否应该重构，比较保守修复和彻底重构方案。",
        "expected_method": "consensus_3",
        "expected_huluwa": 9,
    },
    {
        "id": "single-translate",
        "task": "翻译: hello hive, keep the answer short.",
        "expected_method": "classify_round_robin:translate",
        "expected_huluwa": 4,
    },
    {
        "id": "single-simple",
        "task": "用一句话解释蜂巢是什么。",
        "expected_method": "classify_round_robin:general",
        "expected_huluwa": 8,
    },
]

SWARM_IDS = {"swarm-long-form", "swarm-research-report", "swarm-code-review"}
SMART_IDS = {"smart-finance", "smart-realtime", "smart-code"}
CONSENSUS_IDS = {"consensus-stock-buy", "consensus-architecture"}


def task_id_from_text(task_text: str) -> str:
    for item in TASKS:
        if item["task"] == task_text:
            return item["id"]
    raise AssertionError(f"unknown task text: {task_text[:80]}")


def fake_should_run_collab(task_text: str, task_type: str) -> bool:
    return task_id_from_text(task_text) in SWARM_IDS


def fake_route_via_swarm_skill(task_text: str, task_type: str):
    task_id = task_id_from_text(task_text)
    if task_id not in SWARM_IDS:
        return None
    return {
        "skill_id": 1000 + len(task_id),
        "skill_name": task_id,
        "step_plan": [{"role": "mock", "huluwa_id": expected_huluwa(task_id)}],
        "use_count": 3,
        "success_count": 3,
    }


def fake_run_collab(task_text: str, task_id=None, timeout=300, forced_plan=None):
    routed_id = task_id_from_text(task_text)
    assert routed_id in SWARM_IDS, f"unexpected collab route for {routed_id}"
    hid = expected_huluwa(routed_id)
    return {
        "ok": True,
        "final_result": f"mock swarm result for {routed_id}",
        "total_duration_ms": 11,
        "confidence": 0.91,
        "collab_id": 9000 + hid,
        "steps": [{"role": "verifier", "huluwa_id": hid}],
    }


def fake_should_use_smart_cluster(task_text: str, task_type=None) -> bool:
    return task_id_from_text(task_text) in SMART_IDS


def fake_run_smart_cluster(task_text: str, task_id=None, task_type=None, timeout=300):
    routed_id = task_id_from_text(task_text)
    assert routed_id in SMART_IDS, f"unexpected smart route for {routed_id}"
    hid = expected_huluwa(routed_id)
    scent = {10: "finance", 11: "code", 13: "realtime"}.get(hid, "general")
    return {
        "ok": True,
        "content": f"mock smart result for {routed_id}",
        "wall_ms": 12,
        "confidence": 0.88,
        "run_id": 8000 + hid,
        "primary": hid,
        "fallback_chain": [hid, 9, 12],
        "task_scent": scent,
        "success_hid": hid,
        "fallback_used": False,
    }


def fake_should_run_consensus(task_text: str, task_type: str) -> bool:
    return task_id_from_text(task_text) in CONSENSUS_IDS


def fake_run_consensus(task_text: str, task_id=None, timeout=300):
    routed_id = task_id_from_text(task_text)
    assert routed_id in CONSENSUS_IDS, f"unexpected consensus route for {routed_id}"
    hid = expected_huluwa(routed_id)
    return {
        "ok": True,
        "final_answer": f"mock consensus result for {routed_id}",
        "total_duration_ms": 13,
        "consensus_score": 0.86,
        "run_id": 7000 + hid,
        "winner_huluwa_id": hid,
        "review_triggered": False,
        "top1_score": 0.86,
        "top2_score": 0.71,
    }


def fake_run_one(hid: int, prompt: str, timeout=300):
    return {
        "huluwa": hid,
        "name": f"mock-{hid}",
        "ok": True,
        "content": f"mock single result huluwa-{hid}",
        "wall_ms": 14,
    }


def fake_hk_add(**kwargs):
    return 4242


def expected_huluwa(task_id: str) -> int:
    for item in TASKS:
        if item["id"] == task_id:
            return item["expected_huluwa"]
    raise AssertionError(f"unknown task id: {task_id}")


def main() -> None:
    tasks = [{"id": item["id"], "task": item["task"]} for item in TASKS]
    hive_dispatch._classify_rr_state.clear()
    patches = [
        patch("hive_dispatch.load_scent_embeddings", return_value={}),
        patch("hive_dispatch.increment_load", return_value=None),
        patch("hive_dispatch.record_task", return_value=None),
        patch("hive_dispatch.update_huluwa_task_pheromone", return_value=None),
        patch("hive_dispatch.run_one", side_effect=fake_run_one),
        patch("hive_dispatch.hk.add", side_effect=fake_hk_add),
        patch("hive_dispatch.hk.query", return_value=[]),
        patch("hive_dispatch.hk.update_memory", return_value=None),
        patch("hive_dispatch.hk.extract_experience", return_value={"root_cause": "mock"}),
        patch("hive_dispatch.HiveDispatch._pick", side_effect=lambda text, exclude=None: (4, 0.60, "classify_round_robin:translate") if "翻译" in text else (8, 0.60, "classify_round_robin:general")),
        patch("hive_emergence.route_via_swarm_skill", side_effect=fake_route_via_swarm_skill),
        patch("hive_emergence.record_swarm_skill_usage", return_value=None),
        patch("hive_collab.should_run_collab", side_effect=fake_should_run_collab),
        patch("hive_collab.run_collab", side_effect=fake_run_collab),
        patch("hive_smart_cluster.should_use_smart_cluster", side_effect=fake_should_use_smart_cluster),
        patch("hive_smart_cluster.run_smart_cluster", side_effect=fake_run_smart_cluster),
        patch("hive_consensus.should_run_consensus", side_effect=fake_should_run_consensus),
        patch("hive_consensus.run_consensus", side_effect=fake_run_consensus),
    ]
    with patches[0]:
        for active_patch in patches[1:]:
            active_patch.start()
        try:
            dispatch = HiveDispatch(concurrency=3, timeout=1)
            results = dispatch.run(tasks)
        finally:
            for active_patch in reversed(patches[1:]):
                active_patch.stop()

    failures = []
    for item, result in zip(TASKS, results):
        method = result.get("match_method")
        expected_method = item["expected_method"]
        method_ok = method.startswith(expected_method) if expected_method.endswith("_") else method == expected_method
        huluwa_ok = result.get("huluwa") == item["expected_huluwa"]
        if not method_ok or not huluwa_ok:
            failures.append(
                f"{item['id']}: expected method={expected_method} huluwa={item['expected_huluwa']}, "
                f"got method={method} huluwa={result.get('huluwa')} result={result}"
            )
        print(f"OK {item['id']}: {method} huluwa-{result.get('huluwa')}")

    assert not failures, "\n".join(failures)
    print("PASS integration_10tasks: 3 swarm_skill / 3 smart_cluster / 2 consensus / 2 single")


if __name__ == "__main__":
    main()
