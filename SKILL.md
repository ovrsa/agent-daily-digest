---
name: llm-daily-digest
description: Run or rehearse the agent-daily-digest pipeline, which builds an evidence-backed Japanese daily digest for developers who build AI agents and coding agents. Use when the user wants to run today's digest by hand, see what would be published without publishing (dry run), or inspect the latest run's results.
origin: user
---

# Agent / Coding Agent Daily Digest

毎朝の定期実行はローカル Mac の launchd が行う（`README.md` の「定期実行」）。この Skill は手動で起動するときの手順。

リポジトリは `ovrsa/agent-daily-digest`。この SKILL.md からの相対パスで参照する。

## When to Activate

- `/llm-daily-digest`
- 「今日のダイジェストを手動で回して」「公開せずに結果を見たい」「昨日の実行結果を確認して」

## 手順

1. 既定は dry-run。公開しない:

   ```bash
   ./scripts/run-local.sh --dry-run --max-articles 5
   ```

   結果は `logs/dry-run/<run_id>/`（`digests/<日付>.md`、`comment.md`、`commit.txt`、`metrics/`）。

2. 本番の実行（main への push とコミットコメント）は、ユーザーが明示したときだけ行う:

   ```bash
   ./scripts/run-local.sh
   ```

3. 実行後は、出力の最後にある `run:` と `status:` と、dry-run の出力先をユーザーに報告する。

## 確認に使う場所

- 実行ログ: `logs/run-<日付>.log`
- メトリクス: `logs/metrics/<run_id>.json`
- Judge のレポート: ダイジェストのコミットへのコメント（dry-run では `comment.md`）
