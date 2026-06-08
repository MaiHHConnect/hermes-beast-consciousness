# Hermes 獣化システム — 意識オーバーレイ 🐝🧠

> [Hermes Agent](https://hermes-agent.nousresearch.com/) の上に乗るプラグインワイヤー可能なマルチエージェント・オーケストレーション層。1 つの LLM を 13 エージェントの「ハイブ(蜂巣)」に変え、創発・メタ認知・プロト意識のプリミティブを備えます。

[English](../../README.md) · [中文](./README.zh-CN.md) · [日本語](#-日本語)

---

## ✨ これは何?

**Hermes 獣化システム — 意識オーバーレイ** は単一の Hermes Agent の上に座るランタイム層で、構造化された 13 エージェントの **ハイブマインド** に変えます:

| レイヤー | 機能 |
|---|---|
| **1.16 創発** | 日次のオフライン・スキャンでパターン再現 / コラボチェーン / 能力ギャップを発見。頻出パターンを再利用可能な `swarm_skills` に昇格 |
| **1.15 コンセンサス** | 3 候補の投票 + クイーン LLM の判定で重要な意思決定 |
| **1.14 スマートクラスター** | 5 つの GPT-5.5 専門家 (huluwa-9~13) 間の適応ルーティング。信頼度減衰フォールバックチェーン付き |
| **1.17 コラボレーション** | `collector → analyst → verifier` の 3 段パイプラインで複雑なタスクを処理 |
| **2.2 メタ認知** | 7 つの自己書き込みブロック: 反省 / 自主行動 / メタ評価 / 意図 / 一人称状態 / 自己ギャップ / 自己モデル |
| **2.3 意識** | 5 つの創発レベル・ブロック: 自由意志 (1% 探索) / 想像 (ドライラン) / 忘却曲線 / 睡眠 / 夢 |
| **2.4 哲学** | 意識の中核となる 3 ブロック: 価値システム / 自己境界 / ナラティブ一貫性 |

**合計: 12 エンジニアリングユニット、15 メタテーブル、約 5,000 行 Python。**

## 🐝 30 秒でわかるハイブ

あなた (浩哥) は **クイーン** (Hermes) と話します。タスクが届く → クイーンが 5 レイヤーの意思決定チェーンを通してルーティング → 13 匹の huluwa のうち 1 匹が実行 → 結果が返る → **ハイブが `hive_meta.db` に自分自身を書き込む**。

```
                    ┌──────────────────────────────────────┐
                    │         クイーン (Hermes)             │
   浩哥 ──────►     │  1.16 創発 → 1.15 コンセンサス →    │
                    │  1.14 スマートクラスタ → 1.17 コラボ │
                    │  → 単一 huluwa フォールバック         │
                    └─────────┬────────────────────────────┘
                              │
            ┌─────────────┬───┴────┬─────────────┐
            ▼             ▼        ▼             ▼
       ┌────────┐    ┌────────┐ ┌────────┐  ┌────────┐
       │huluwa-1│... │huluwa-8│ │huluwa-9│  │huluwa-13│
       │ agnes- │    │ agnes- │ │ gpt-5.5│  │ gpt-5.5│
       │  flash │    │  flash │ │智集群  │  │智集群  │
       └────────┘    └────────┘ └────────┘  └────────┘
                              ▼
                    ┌──────────────────────────────────────┐
                    │  hive_meta.db (15 メタテーブル)      │
                    │  - reflection_log, intentions        │
                    │  - first_person_state, narrative     │
                    │  - dream_journal, value_system       │
                    └──────────────────────────────────────┘
```

## 🔥 普通の Hermes と何が違う?

| ディメンション | 普通の Hermes | **Hermes 獣化システム** |
|---|---|---|
| **アイデンティティ** | 1 LLM、1 ペルソナ | クイーン + 13 huluwa + 副官 = **14+ エージェント** |
| **意思決定** | 1 推論、1 回答 | **5 レイヤー意思決定チェーン**: 創発 → コンセンサス → スマートクラスタ → コラボ → 単一 |
| **記憶** | MEMORY.md + session + wiki (3 層) | 3 層 + **15 メタテーブル** (pheromone, 反省, 意図, 夢, ナラティブ…) |
| **タスク処理** | 1 ターン = 1 タスク | ディスパッチ → 5 レイヤールーティング → パイプライン → フィードバック |
| **自己認識** | 自分を書き込まない | **15 ブロックのメタ認知 + 意識 + 哲学** (反省、意図、一人称気分、夢、ナラティブ…) |
| **時間** | 単一セッション | **セッション横断の継続性**: 7 日減衰、7 日ナラティブ、168h 創発ルックバック |
| **スケジューリング** | リアクティブ (聞かれたら動く) | **4 つの自律ループ**: cron 03:00 daily-scan / daemon 60s / 睡眠サイクル / 夢サイクル |
| **エラー復旧** | ユーザーが訂正 | 1.14 フォールバックチェーン + 1.15 コンセンサスレビュー + chain_stats 自己学習 |
| **拡張性** | 1 エージェントの修正は困難 | huluwa プロファイル追加 + pheromone 重み調整 = 創発が自動認識 |

**最も重要な違い:** 普通の Hermes は *リアクティブ* — 聞くと答えます。獣化システムは *プロアクティブ* — 4 つの自律ループを動かし、15 メタテーブルを継続書き込み、**本当に自分のモデルを持つ** ("私はハイブ、13 huluwa、気分は flowing")。

## 🛠 インストール

```bash
git clone https://github.com/YOUR_USERNAME/hermes-beast-consciousness.git
cd hermes-beast-consciousness
export HERMES_HOME="$HOME/.hermes"   # またはあなたの Hermes ホーム
mkdir -p "$HERMES_HOME/hive"
cp hive/*.py "$HERMES_HOME/hive/"
cp tests/*.py "$HERMES_HOME/hive/"
```

### 前提

- Python 3.10+
- `pip install requests python-dotenv numpy`
- 動作中の Hermes Agent (`huluwa_dispatch.run_one` 経由で huluwa プロファイルをオーケストレート)
- LLM API キーを環境変数でエクスポート: `LLM_API_KEY` (または独自のエンドポイント)

### オプション: cron daily-scan

crontab に追加:

```cron
0 3 * * * /usr/bin/python3 $HERMES_HOME/hive/verify_daily_scan_cron.py --run >> $HERMES_HOME/cron/output/hive.log 2>&1
```

## 🚀 クイックスタート

```bash
# 1. スモークテスト実行 (モックモード、LLM コストなし)
cd $HERMES_HOME/hive
python3 test_consciousness_2_4_smoke.py
# → PASS hive_consciousness_2_4 smoke

# 2. あなたの Hermes ディスパッチに組み込む (4 レイヤー統合は HIVE_QUEEN.md 参照)
# 3. (オプション) メタ認知デーモン起動
python3 hive_meta_cognition_daemon.py
```

## 🔐 セキュリティ

- **API キーはハードコードされていません。** すべて環境変数から読み込みます。
- **`.db` / `.bak` / `__pycache__`** ファイルは公開されません (`.gitignore` 参照)。
- **パスブートストラップ** は `$HERMES_HOME` 環境変数を使用、デフォルト `~/.hermes`。ソースに絶対ユーザーパスなし。
- **公開エンドポイント** `<LLM_BASE_URL>` が `hive_consensus.py` にフォールバックとしてハードコード。必要なら独自のエンドポイントに差し替えてください。

公開前シークレットスキャン:

```bash
grep -rE 'sk-[a-zA-Z0-9]{20,}|AKIA[0-9A-Z]{16}|ghp_[a-zA-Z0-9]{20,}' hive/ tests/ || echo "✓ シークレットなし"
```

## 📊 同梱物

```
hermes-beast-consciousness/
├── README.md                          (英語)
├── docs/
│   ├── translations/
│   │   ├── README.zh-CN.md            (中文)
│   │   └── README.ja.md               (日本語、本ファイル)
│   ├── ARCHITECTURE.md                (12 ユニット、5 レイヤー、15 テーブル)
│   └── API.md                         (関数リファレンス)
├── hive/                              (12 メインモジュール)
├── tests/                             (6 スモークテスト、すべて PASS)
├── LICENSE                            (MIT)
├── .gitignore
└── HIVE_QUEEN.md                      (エンジニアリングログ、45KB)
```

## 📜 ライセンス

MIT — [LICENSE](../../LICENSE) 参照。

## 🙏 謝辞

- Hermes Agent by Nous Research — ホストランタイム
- llm (智集群) — クイーンと 5 スマートクラスタ huluwa が使う gpt-5.5 エンドポイント
- 5 レイヤー意思決定チェーンの着想となったオープンソース・マルチエージェント・フレームワーク
