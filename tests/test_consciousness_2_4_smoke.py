#!/usr/bin/env python3
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

import sqlite3
import sys
from pathlib import Path

HIVE_DIR = Path(os.environ.get("HERMES_HOME", "~/.hermes")) / ".hermes/hive"
sys.path.insert(0, str(HIVE_DIR))

from hive_consciousness_2_4 import (
    HIVE_META_DB,
    check_narrative_consistency,
    classify_entity,
    express_value,
    get_recent_narrative,
    init_consciousness_2_4_db,
    narrate_thread,
    rank_values,
    reinforce_value,
    who_am_i,
)


def count_rows(table: str) -> int:
    with sqlite3.connect(str(HIVE_META_DB), timeout=5.0) as conn:
        row = conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()
        return int(row[0] if row else 0)


def main() -> int:
    try:
        init_consciousness_2_4_db()

        express_value('completion', 4.0, 'self', 'smoke completion')
        express_value('correctness', 4.0, 'self', 'smoke correctness')
        express_value('robustness', 4.0, 'self', 'smoke robustness')
        for _ in range(5):
            reinforce_value('correctness', 0.5)
        ranked = rank_values()
        assert count_rows('value_system') >= 3, 'value_system rows < 3'
        assert ranked and ranked[0]['value_name'] == 'correctness', f'value rank wrong: {ranked[:2]}'

        classify_entity(os.path.join(os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes")), ".hermes/hive/hive_smart_cluster.py"), 'module', 'smoke hive module')
        classify_entity(os.path.join(os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes")), ".hermes/hive/hive_meta_cognition.py"), 'module', 'smoke hive module')
        classify_entity(os.path.join(os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes")), ".hermes/profiles/huluwa-9/agent.py"), 'agent', 'smoke huluwa profile')
        classify_entity('浩哥', 'user', 'smoke user')
        classify_entity('llm', 'external_api', 'smoke api')
        identity = who_am_i()
        assert count_rows('self_boundary') >= 5, 'self_boundary rows < 5'
        assert len(identity['self_entities']) >= 3, f'self entities < 3: {identity}'

        narrate_thread('我今天学到了什么', lookback_hours=24)
        narrate_thread('我近 7 天的成长', lookback_hours=168)
        recent = get_recent_narrative(limit=3)
        consistency = check_narrative_consistency()
        assert count_rows('narrative_thread') >= 1, 'narrative_thread rows < 1'
        assert len(recent) >= 1, 'recent narrative empty'
        assert isinstance(consistency, dict) and 'consistent' in consistency, 'consistency not dict'

        print('PASS hive_consciousness_2_4 smoke')
        print('top_value=', ranked[0]['value_name'], 'self_count=', len(identity['self_entities']), 'narratives=', len(recent), 'consistent=', consistency['consistent'])
        return 0
    except Exception as exc:
        print('FAIL hive_consciousness_2_4 smoke:', exc)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
