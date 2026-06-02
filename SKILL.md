---
name: llm-daily-digest
description: Generate a Japanese daily digest of hot LLM / Coding Agent news from 8 high-signal sources (Simon Willison, AI News by smol.ai, Latent Space, Interconnects, HF Daily Papers, Hacker News, Reddit r/LocalLLaMA·ClaudeAI·cursor·MachineLearning, GitHub releases for Claude Code/Aider/Cline/Continue/Codex/SWE-agent/Goose). Use when the user wants today's LLM/Coding Agent digest, asks to run the daily digest manually, or wants to inspect what would be published.
origin: user
---

# LLM / Coding Agent Daily Digest

8 ソースから新着を集め、日本語 Markdown ダイジェストを 1 本生成する。通常は claude.ai のリモート routine で毎朝自動実行されるが、アドホックにも起動できる。

リポジトリは `ovrsa/agent-daily-digest`。この SKILL.md からの相対パスでファイルを参照する（`src/`, `prompts/`, `config/`）。

## When to Activate

- `/llm-daily-digest`
- 「今日の LLM まとめ」「Coding Agent の最新情報まとめて」「今日のダイジェスト作って」
- スケジュールに頼る前に手動で出力を確認したいとき

## Sources

| # | Source | Type | Endpoint |
|---|---|---|---|
| 1 | Simon Willison | Atom | `simonwillison.net/atom/everything/` |
| 2 | AI News by smol.ai | RSS | `buttondown.com/ainews/rss` |
| 3 | Latent Space | RSS | `latent.space/feed` |
| 4 | Interconnects (Nathan Lambert) | RSS | `interconnects.ai/feed` |
| 5 | HF Daily Papers | JSON | `huggingface.co/api/daily_papers` |
| 6 | Hacker News (Algolia) | JSON | keyword + last-24h filter |
| 7 | Reddit | Atom | `r/LocalLLaMA`, `r/ClaudeAI`, `r/cursor`, `r/MachineLearning` top/day |
| 8 | GitHub Releases | JSON | config の `gh_repos` |

ソース・キーワード・対象 repo は `config/config.json` で調整する。

## Ad-hoc Invocation Workflow

対話的に起動されたとき:

1. `config/config.json` の `digest_dir`（出力先・repo 相対）を確認。
2. fetcher を実行:
   ```bash
   python3 src/fetch.py --config config/config.json --out /tmp/llm-digest-raw-$(date +%Y-%m-%d).json
   ```
3. その JSON を Read する。
4. `prompts/system-prompt.md` の編集ワークフローに従ってダイジェストを生成。
5. `digests/{YYYY-MM-DD}.md` に Write する。
6. 出力パスをユーザーに報告。

## Scheduled (Cloud Routine) Invocation

毎朝の自動実行は claude.ai のリモート routine が担う。本体プロンプトは `routine/prompt.md`。流れ:

1. cron で claude.ai routine が起動し、repo を checkout。
2. `python3 src/fetch.py` → raw JSON。
3. `prompts/system-prompt.md` に従いダイジェスト生成 → `digests/YYYY-MM-DD.md`。
4. `digests/README.md` の index を更新。
5. `git commit & push origin main`。

登録手順は `README.md` を参照。

## Configuration（`config/config.json`）

- `digest_dir` — 出力先ディレクトリ（repo 相対 or 絶対）。デフォルト `digests`。
- `max_items_per_source` — ソース別取得上限（既定 12）。編集フィルタで更に絞る。
- `summary_model` — `claude -p` のモデル。既定 `claude-sonnet-4-6`、軽量化なら `claude-haiku-4-5`。
- `sources.{name}` — ソース別 ON/OFF。
- `hackernews_keywords` — HN 検索キーワード（直近24h・points>30）。
- `reddit_subs` — 取得対象 subreddit。
- `gh_repos` — リリース追跡する `owner/repo`。

## Files

```
agent-daily-digest/
├── SKILL.md                  # このファイル
├── README.md                 # セットアップ / 運用
├── AGENTS.md                 # repo-local map
├── config/config.json        # 設定
├── src/fetch.py              # stdlib のみの収集スクリプト
├── prompts/system-prompt.md  # 編集者ロール定義
├── routine/prompt.md         # リモート routine 本体プロンプト
├── digests/                  # 出力（YYYY-MM-DD.md + index）
├── scripts/run-local.sh      # ローカル手動実行
└── docs/legacy-launchd/      # 旧 launchd plist（参考保存）
```
