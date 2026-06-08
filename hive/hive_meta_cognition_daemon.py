from __future__ import annotations
#!/usr/bin/env python3
"""可选元认知 daemon：60s 同步轮询主动发起动作；不自动启动."""
# === hermes-hive path bootstrap ===
import os
_HERMES_HOME = os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")
_HIVE_DIR = os.path.join(_HERMES_HOME, "hive")
if _HIVE_DIR not in sys.path:
    sys.path.insert(0, _HIVE_DIR)
if _HERMES_HOME not in sys.path:
    sys.path.insert(0, _HERMES_HOME)
# === end bootstrap ===



import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from hive_meta_cognition import discover_and_act, init_meta_cognition_db
from hive_consciousness_2_3 import sleep_cycle
from hive_consciousness_2_4 import narrate_thread


def main(interval: int = 60) -> None:
    init_meta_cognition_db()
    loop_count = 0
    while True:
        try:
            actions = discover_and_act()
            if actions:
                print(f"[hive_meta_daemon] created_actions={len(actions)}", flush=True)
            loop_count += 1
            if loop_count % 10 == 0:
                sleep_result = sleep_cycle(duration_min=1)
                print(f"[hive_meta_daemon] sleep_cycle={sleep_result}", flush=True)
            if loop_count % 30 == 0:
                narrative_id = narrate_thread("我近 7 天的成长", lookback_hours=168)
                print(f"[hive_meta_daemon] narrative_7d_id={narrative_id}", flush=True)
        except Exception as exc:
            print(f"[hive_meta_daemon] error={exc}", file=sys.stderr, flush=True)
        time.sleep(interval)


if __name__ == "__main__":
    main()