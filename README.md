# agent-daily-digest

毎朝 LLM / Coding Agent の最新動向を 8 ソースから収集し、日本語ダイジェスト Markdown を 1 本生成して **GitHub リポジトリにコミット**する。

スケジュール実行は **claude.ai のリモート routine**（クラウドで Claude Code を cron 起動）で行うため、Mac が起動している必要はない。生成物は `digests/YYYY-MM-DD.md` として main に直接コミットされる。

## 仕組み

```
[毎朝 cron] claude.ai routine (cloud)
  └─ repo を checkout
       ├─ python3 src/fetch.py        … 8 ソースを収集し raw JSON 出力 (stdlib のみ)
       ├─ prompts/system-prompt.md    … 編集者ルールで日本語ダイジェスト生成
       ├─ digests/YYYY-MM-DD.md       … 出力
       ├─ digests/README.md           … index 更新
       └─ git commit & push origin main
```

routine の本体プロンプトは [`routine/prompt.md`](./routine/prompt.md)（真実源）。

## フォルダ構成

```
agent-daily-digest/
├── README.md                 # このファイル
├── AGENTS.md                 # repo-local map（真実源へのポインタ）
├── SKILL.md                  # /llm-daily-digest アドホック起動用 Skill 定義
├── config/config.json        # ソース ON/OFF・キーワード・対象 repo・モデル
├── src/fetch.py              # stdlib のみの 8 ソース収集スクリプト
├── prompts/system-prompt.md  # 編集者ロール定義（日本語要約フォーマット）
├── routine/prompt.md         # リモート routine 本体プロンプト
├── digests/                  # ★ 出力（YYYY-MM-DD.md + index README）
├── scripts/run-local.sh      # ローカル手動実行（fetch + claude -p）
└── docs/legacy-launchd/      # 旧 launchd plist（参考保存）
```

## セットアップ

### 1. claude.ai に GitHub 連携を有効化

claude.ai の Claude Code (web) 設定で、この `ovrsa/agent-daily-digest` リポジトリへのアクセスを許可する。routine が repo を checkout し main に push できる状態にする。

### 2. routine を登録

Claude Code セッションで `/schedule` を使い、毎朝の routine を作成する。prompt は [`routine/prompt.md`](./routine/prompt.md) の内容、対象リポジトリは `ovrsa/agent-daily-digest`、cron は例: `7 8 * * *`（毎朝 08:07 ローカル）。

### 3. 初回は手動 run で観測

登録後すぐに routine を 1 回手動実行し、クラウド環境で以下を確認する:

- 8 ソースへのアウトバウンドネットワークが通るか（特に reddit）
- `python3` (3.10+) が使えるか
- `digests/YYYY-MM-DD.md` が生成され main に push されるか

## ローカル手動実行

クラウドを使わずローカルで 1 回生成したいとき:

```bash
./scripts/run-local.sh
```

- `fetch.py` → `claude -p`（ログイン済み Claude Code セッション認証を使用）の順で実行。
- 出力は `config/config.json` の `digest_dir`（デフォルト `digests`、repo 相対）配下の `YYYY-MM-DD.md`。
- ログ: `logs/run-YYYY-MM-DD.log`。

## カスタマイズ（`config/config.json`）

| キー | 説明 |
|---|---|
| `digest_dir` | 出力先ディレクトリ（repo 相対 or 絶対）。デフォルト `digests` |
| `max_items_per_source` | ソース別の取得上限（既定 12）。編集フィルタで更に絞られる |
| `summary_model` | `claude -p` のモデル。既定 `claude-sonnet-4-6`、軽くするなら `claude-haiku-4-5` |
| `sources.{name}` | ソース別 ON/OFF |
| `hackernews_keywords` | HN 検索キーワード（直近24h・points>30） |
| `reddit_subs` | 取得対象 subreddit |
| `gh_repos` | リリース追跡する `owner/repo` |

## ソース

Simon Willison / AI News (smol.ai) / Latent Space / Interconnects / HF Daily Papers / Hacker News / Reddit (LocalLLaMA, ClaudeAI, cursor, MachineLearning) / GitHub Releases。

## Legacy: launchd（ローカル定期実行）

リモート routine 移行前は launchd でローカル定期実行していた。plist は [`docs/legacy-launchd/`](./docs/legacy-launchd/) に参考保存。ローカル定期実行に戻したい場合のみ使用する（Mac の起動が前提）。

## トラブルシュート

- **reddit が取得できない**: クラウド IP がブロックされている可能性。`config/config.json` で `sources.reddit` を `false` にするか、routine 側で失敗ソースをスキップして継続する。
- **`python3` が見つからない**: ローカル launchd 経路の場合は plist の `PATH` に `which python3` の親を追加。
- **`claude` が見つからない（ローカル）**: `which claude` を確認し PATH に追加。`run-local.sh` はログイン済みセッション認証を使う。
