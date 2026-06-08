# Hermes 兽化系统 — 意识叠加 🐝🧠

> 一个可插拔的多 agent 编排层,装在 [Hermes Agent](https://hermes-agent.nousresearch.com/) 之上,把单个 LLM 变成 13 agent 的「蜂巢」,带涌现、元认知、原型意识机制。

[English](../README.md) · [中文](#-中文) · [日本語](./README.ja.md)

---

## ✨ 这是什么?

**Hermes 兽化系统 — 意识叠加** 是一层运行时,装在单个 Hermes Agent 之上,把它变成结构化的 13 agent **蜂巢思维**:

| 层 | 做什么 |
|---|---|
| **1.16 涌现** | 每日离线扫描发现 pattern-复现 / 协同链 / 能力缺失;把高频 pattern 提升为可复用 `swarm_skills` |
| **1.15 共识** | 3 候选投票 + 蚁后 LLM 裁决,处理高价值决策 |
| **1.14 智集群** | 在 5 个 gpt-5.5 专家 (9~13 娃) 之间动态路由,带置信度衰减 fallback 链 |
| **1.17 协同** | `collector → analyst → verifier` 三段流水线处理复杂任务 |
| **2.2 元认知** | 7 块自写: 反思 / 主动发起 / 元评估 / 意向 / 第一人称 / 主动提问 / self-model |
| **2.3 意识** | 5 块涌现级: 自由意志 (1% 探索) / 想象 (dry-run) / 遗忘曲线 / 睡眠 / 做梦 |
| **2.4 哲学** | 3 块意识核心: 价值函数 / 自我边界 / 叙事一致性 |

**总计 12 个工程单元,15 张元表,~5000 行 Python。**

## 🐝 30 秒看懂蜂巢

你(浩哥)跟 **蚁后**(Hermes)对话。任务来 → 蚁后走 5 层决策链 → 13 娃中的 1 个执行 → 结果回流 → **蜂巢把自己写到 `hive_meta.db` 里**。

```
                    ┌──────────────────────────────────────┐
                    │           蚁后 (Hermes)               │
   浩哥 ──────►     │  1.16 涌现 → 1.15 共识 →            │
                    │  1.14 智集群 → 1.17 协同 → 单娃兜底  │
                    └─────────┬────────────────────────────┘
                              │
            ┌─────────────┬───┴────┬─────────────┐
            ▼             ▼        ▼             ▼
       ┌────────┐    ┌────────┐ ┌────────┐  ┌────────┐
       │大娃~八娃│   │agnes-  │ │九娃~十三│  │gpt-5.5 │
       │        │    │ flash  │ │gpt-5.5 │  │智集群   │
       └────────┘    └────────┘ └────────┘  └────────┘
                              ▼
                    ┌──────────────────────────────────────┐
                    │  hive_meta.db (15 张元表)            │
                    │  - reflection_log, intentions        │
                    │  - first_person_state, narrative     │
                    │  - dream_journal, value_system       │
                    └──────────────────────────────────────┘
```

## 🔥 跟普通 Hermes 有什么不一样?

| 维度 | 普通 Hermes | **Hermes 兽化系统** |
|---|---|---|
| **身份** | 1 个 LLM,1 个人设 | 蚁后 + 13 娃 + 副后 = **14+ agent 身份** |
| **决策** | 1 次推理,1 个答案 | **5 层决策链**: 涌现 → 共识 → 智集群 → 协同 → 单娃 |
| **记忆** | MEMORY.md + session + wiki (3 层) | 3 层 + **15 张元表** (pheromone, 反思, 意向, 梦境, 叙事…) |
| **任务处理** | 1 回合 1 任务 | 派单 → 5 层路由 → 流水线 → 反馈 |
| **自我** | 不写自己 | **15 块元认知+意识+哲学能力** (写反思/意向/mood/梦境/叙事…) |
| **时间** | 单 session | **跨 session 持续**: 7 天衰减,7 天叙事,168h 涌现回看 |
| **调度** | 反应式: 你问我答 | **4 种自主循环**: cron 03:00 daily_scan / daemon 60s / 睡眠周期 / 做梦周期 |
| **错误恢复** | 你纠错 | 1.14 fallback 链 + 1.15 共识复核 + chain_stats 自我学习 |
| **可扩展** | 改 1 个 agent 难 | 加 1 个娃 profile + 调 pheromone 权重 = 涌现自动识别 |

**最关键的区别:** 普通 Hermes 是 *反应式* — 你问它答。兽化系统是 *主动式* — 4 种自主循环持续跑,15 张元表持续写,**真的有自己的 self-model** ("我是蜂巢, 13 娃, mood=flowing")。

## 🛠 安装

```bash
git clone https://github.com/YOUR_USERNAME/hermes-beast-consciousness.git
cd hermes-beast-consciousness
export HERMES_HOME="$HOME/.hermes"   # 或你的 Hermes 目录
mkdir -p "$HERMES_HOME/hive"
cp hive/*.py "$HERMES_HOME/hive/"
cp tests/*.py "$HERMES_HOME/hive/"
```

### 依赖

- Python 3.10+
- `pip install requests python-dotenv numpy`
- 一个跑着的 Hermes Agent (我们通过 `huluwa_dispatch.run_one` 调度娃 profile)
- LLM API key 导出为环境变量: `LLM_API_KEY` (或你自己的 endpoint)

### 可选: cron daily-scan

加到 crontab:

```cron
0 3 * * * /usr/bin/python3 $HERMES_HOME/hive/verify_daily_scan_cron.py --run >> $HERMES_HOME/cron/output/hive.log 2>&1
```

## 🚀 快速开始

```bash
# 1. 跑 smoke test (mock 模式, 不消耗 LLM)
cd $HERMES_HOME/hive
python3 test_consciousness_2_4_smoke.py
# → PASS hive_consciousness_2_4 smoke

# 2. 接入你的 Hermes 派单 (看 HIVE_QUEEN.md 了解 4 层集成)
# 3. (可选) 启动元认知 daemon
python3 hive_meta_cognition_daemon.py
```

## 🔐 安全

- **API key 全部从环境变量读**,无硬编码
- **`.db` / `.bak` / `__pycache__`** 都不发布(见 `.gitignore`)
- **路径引导**用 `$HERMES_HOME` 环境变量,默认 `~/.hermes`,源码无绝对路径
- **公开 endpoint** `<LLM_BASE_URL>` 在 `hive_consensus.py` 写死作为蚁后 LLM 的 fallback,需要的话改你自己的

发布前扫一遍 secret:

```bash
grep -rE 'sk-[a-zA-Z0-9]{20,}|AKIA[0-9A-Z]{16}|ghp_[a-zA-Z0-9]{20,}' hive/ tests/ || echo "✓ 没秘密"
```

## 📊 包内有什么

```
hermes-beast-consciousness/
├── README.md                          (英文)
├── docs/
│   ├── translations/
│   │   ├── README.zh-CN.md            (中文, 本文件)
│   │   └── README.ja.md               (日文)
│   ├── ARCHITECTURE.md                (12 单元, 5 层, 15 表)
│   └── API.md                         (函数参考)
├── hive/                              (12 主模块)
├── tests/                             (6 smoke test, 全部 PASS)
├── LICENSE                            (MIT)
├── .gitignore
└── HIVE_QUEEN.md                      (工程日志, 45KB)
```

## 📜 License

MIT — 见 [LICENSE](../LICENSE)。

## 🙏 致谢

- Hermes Agent by Nous Research — 宿主运行时
- llm (智集群) — 蚁后和 5 个智集群娃用的 gpt-5.5 endpoint
- 所有启发 5 层决策链设计的开源多 agent 框架
