#!/usr/bin/env python3
"""验证蜂巢 daily_scan cron 配置与本地模拟执行。"""
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

import argparse
import json
import subprocess
import sys
from pathlib import Path

JOBS_PATH = Path(os.environ.get("HERMES_HOME", "~/.hermes")) / ".hermes/cron/jobs.json"
HIVE_DIR = Path(os.environ.get("HERMES_HOME", "~/.hermes")) / ".hermes/hive"
OUTPUT_DIR = Path(os.environ.get("HERMES_HOME", "~/.hermes")) / ".hermes/cron/output"
JOB_NAME = "蜂巢 daily_scan 自进化"


def load_job() -> dict:
    data = json.loads(JOBS_PATH.read_text(encoding="utf-8"))
    jobs = data.get("jobs", [])
    matches = [job for job in jobs if job.get("name") == JOB_NAME]
    assert len(matches) == 1, f"expected exactly 1 {JOB_NAME}, got {len(matches)}"
    return matches[0]


def validate_job(job: dict) -> None:
    assert job.get("enabled") is True, "job.enabled must be true"
    assert job.get("deliver") == "local", "job.deliver must be local"
    schedule = job.get("schedule") or {}
    assert schedule.get("kind") == "cron", "schedule.kind must be cron"
    assert schedule.get("expr") == "0 3 * * *", "schedule.expr must be 0 3 * * *"
    assert schedule.get("display") == "0 3 * * *", "schedule.display must be 0 3 * * *"
    prompt = job.get("prompt") or ""
    required = [
        "cd \"$_HERMES_HOME/hive\"  # cd to $HERMES_HOME/hive",
        "run_daily_scan",
        "daily_scan",
        "reproduction",
        "collab_chain",
        "capability_gap",
        os.path.join(os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes")), ".hermes/cron/output/hive_daily_scan_YYYYMMDD.md"),
    ]
    missing = [item for item in required if item not in prompt]
    assert not missing, f"prompt missing required markers: {missing}"
    model = job.get("model")
    assert model == {"provider": "default"}, f"model must be {{'provider':'default'}}, got {model!r}"


def simulate_daily_scan() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        "-c",
        (
            "import json; "
            "from hive_emergence import daily_scan; "
            "print(json.dumps(daily_scan(), ensure_ascii=False, sort_keys=True))"
        ),
    ]
    result = subprocess.run(cmd, cwd=str(HIVE_DIR), text=True, capture_output=True, timeout=60)
    assert result.returncode == 0, f"daily_scan failed rc={result.returncode}\nSTDOUT={result.stdout}\nSTDERR={result.stderr}"
    payload = json.loads(result.stdout.strip())
    for key in ["reproduction_count", "collab_chain_count", "capability_gap_count"]:
        assert key in payload, f"daily_scan output missing {key}: {payload}"
    print("daily_scan_simulated", json.dumps(payload, ensure_ascii=False, sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="store_true", help="实际执行一次 daily_scan fallback 模拟")
    args = parser.parse_args()

    job = load_job()
    validate_job(job)
    print("jobs_json_valid", JOB_NAME, job["schedule"]["expr"], job["deliver"])
    if args.run:
        simulate_daily_scan()


if __name__ == "__main__":
    main()
