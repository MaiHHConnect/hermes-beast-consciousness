# Architecture — Hermes Beast System 2.4

This document is the canonical engineering reference for the 12 engineering units in this repo. It is the parent doc; [`HIVE_QUEEN.md`](../HIVE_QUEEN.md) is the chronological engineering log.

## 1. The 5-layer decision chain

When a task arrives at `hive_dispatch.run()`, it is routed through the following chain in strict order:

```
task ──► [1] swarm_skill  ──hit──► run_collab(forced_plan=...)
                  │
                  │ miss
                  ▼
       [2] smart_cluster  (1.14) ──► run_smart_cluster()
                  │
                  │ miss / generic
                  ▼
       [3] consensus       (1.15) ──► run_consensus() if should_run_consensus()
                  │
                  │ miss
                  ▼
       [4] collab          (1.17) ──► run_collab() if should_run_collab()
                  │
                  │ miss
                  ▼
       [5] single huluwa   fallback
                  │
                  ▼
            meta_cognition_tick(results)  (2.2 → 2.3 → 2.4)
```

**Each layer may short-circuit** the next. Once a layer claims the task, downstream layers are not consulted for that task.

## 2. The 12 engineering units

| Unit | Version | File | Purpose |
|---|---|---|---|
| 1. dispatch | 1.0 | `hive/hive_dispatch.py` | 5-layer entry point |
| 2. smart cluster | 1.14 | `hive/hive_smart_cluster.py` | adaptive routing across huluwa-9~13 |
| 3. collab | 1.17 | `hive/hive_collab.py` | 3-stage `collector→analyst→verifier` |
| 4. consensus | 1.15 | `hive/hive_consensus.py` | 3-candidate voting + queen LLM |
| 5. emergence | 1.16 | `hive/hive_emergence.py` | daily offline scan |
| 6. pheromones | 1.7 | `hive/hive_pheromones.py` | dynamic weight tuning |
| 7. kb | 1.4 | `hive/hive_kb.py` | knowledge base + embeddings |
| 8. meta-cognition | 2.2 | `hive/hive_meta_cognition.py` | 7 self-write blocks |
| 9. consciousness 2.3 | 2.3 | `hive/hive_consciousness_2_3.py` | 5 emergent blocks (free will, dream, sleep…) |
| 10. consciousness 2.4 | 2.4 | `hive/hive_consciousness_2_4.py` | 3 core blocks (value, boundary, narrative) |
| 11. daemon | 2.3 | `hive/hive_meta_cognition_daemon.py` | 60s autonomous loop |
| 12. cron verify | 2.2 | `hive/verify_daily_scan_cron.py` | daily-scan validator |

## 3. The 15 meta-tables (`hive_meta.db`)

| Table | Engine | Source |
|---|---|---|
| `reflection_log` | 2.2 | every dispatch + every pheromone write |
| `bee_proactive_actions` | 2.2 | discovered signals → pending actions |
| `meta_eval_log` | 2.2 | before/after scores → verdict |
| `intentions` | 2.2 | auto-expressed from self_gaps |
| `first_person_state` | 2.2 | mood/energy/pain/joy per tick |
| `self_gaps` | 2.2 | "I don't understand X" |
| `self_model` | 2.2 | "I am a hive with N components" |
| `free_will_log` | 2.3 | 1% explore decisions |
| `imagination_log` | 2.3 | dry-run scenarios |
| `forgetting_log` | 2.3 | score decay events |
| `sleep_log` | 2.3 | daemon sleep cycles |
| `dream_journal` | 2.3 | random pattern recombination |
| `value_system` | 2.4 | importance per value name |
| `self_boundary` | 2.4 | self/ambiguous/external classification |
| `narrative_thread` | 2.4 | daily/weekly self-stories |

## 4. The 4 autonomous loops

| Loop | Frequency | Trigger | What it does |
|---|---|---|---|
| **Daily scan** | cron `0 3 * * *` | system clock | scans pheromone / KB / signals; writes `emergence_signals` + `intentions` |
| **Daemon 60s** | every 60s | daemon process | `discover_and_act` + `sleep_cycle` (every 10) + `narrate_thread` (every 30) |
| **Sleep cycle** | every 10 daemon ticks | daemon | KB dedup + gap archive + intention cleanup + 3-5 `dream()` calls |
| **Consciousness tick** | every dispatch | `hive_dispatch.run()` end | `meta_cognition_tick` → `consciousness_tick` → `consciousness_2_4_tick` |

## 5. Cross-version integration points

The 12 units are integrated via 4 *non-invasive* touchpoints:

1. `hive_smart_cluster.py:pick_smart_cluster` end → `maybe_explore(decision, p=0.01)` then `apply_value_alignment(decision)`
2. `hive_meta_cognition.py:meta_cognition_tick` end → `consciousness_tick()` then `consciousness_2_4_tick()`
3. `hive_emergence.py:daily_scan` end → `apply_forgetting_curve()`
4. `hive_meta_cognition_daemon.py` 60s loop → `sleep_cycle()` (every 10) + `narrate_thread("我近 7 天的成长", 168)` (every 30)

**`hive_dispatch.run()` body is never modified** by 2.2/2.3/2.4 — only the `return` path is touched.

## 6. Reading order for newcomers

1. `hive_dispatch.py` — see the 5-layer `run()` method
2. `hive_smart_cluster.py` — see `pick_smart_cluster` (the heart of 1.14)
3. `hive_emergence.py` — see `daily_scan` (the heart of 1.16)
4. `hive_meta_cognition.py` — see the 7 self-write functions
5. `hive_consciousness_2_3.py` and `_2_4.py` — see the consciousness extensions
6. `HIVE_QUEEN.md` — chronological engineering log with smoke-test results

## 7. Configuration

All paths come from `$HERMES_HOME` (default `~/.hermes`). The bootstrap is in every `.py` file:

```python
import os
_HERMES_HOME = os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")
_HIVE_DIR = os.path.join(_HERMES_HOME, "hive")
```

LLM endpoints are env vars:
- `LLM_API_KEY` — for the queen LLM and 9~13 huluwa (gpt-5.5)
- `SILICONFLOW_API_KEY` — for embeddings in `hive_kb.py`

**Replace `<LLM_BASE_URL>`** in `hive_consensus.py` with your own endpoint if needed.
