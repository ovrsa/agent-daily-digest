# AGENTS — agent-daily-digest

このリポジトリの薄い repo-local map。詳細はリンク先（真実源）を参照。

## 何をするリポジトリか

毎朝、ローカル Mac の launchd が `scripts/run-local.sh`（`python -m digest_pipeline`）を起動し、ソースの収集から、根拠付きの日本語ダイジェストの `digests/` への main 直コミット、Judge のレビューコメントまでを1回で行う。詳細は [`README.md`](./README.md)、設計の決定は親 Issue #1（Design Doc）。

## 真実源

| 関心 | ファイル |
|---|---|
| 日次実行の流れ（収集から公開、Judge のコメントまで。`python -m digest_pipeline [--dry-run]`） | [`src/digest_pipeline/`](./src/digest_pipeline/) |
| 段と段の間の契約（Pydantic） | [`src/digest_contracts/`](./src/digest_contracts/) |
| 研究・Selector・Judge のプロンプト | `src/digest_research/prompt.py` / `src/digest_select/prompt.py` / `src/digest_judge/prompt.py` |
| ソース・モデル・研究の上限 | [`config/config.json`](./config/config.json) |
| 実行入口（手動・launchd 共通） | [`scripts/run-local.sh`](./scripts/run-local.sh) |
| 定期実行の登録 | [`launchd/`](./launchd/)（`install.sh` がテンプレートを埋めて登録する） |
| 出力アーカイブ | [`digests/`](./digests/) |
| 処理状態（URL・初回発見時刻・本文ハッシュ・直近の採否） | `state/processed.json`（初回の公開で作られる） |

## 変更時の注意

- 採否や掲載文の質を変えたい → `src/digest_select/prompt.py`（`PROMPT_VERSION` を上げ、`evals/selector_fixed_set.py` を実行して表を比べる）
- 監査の観点を変えたい → `src/digest_judge/prompt.py`（同じく `evals/judge_fixed_set.py`）
- 収集対象を変えたい → `config/config.json` の `collection`
- 実行フローや失敗の扱いを変えたい → `src/digest_pipeline/pipeline.py`（failure policy は Design Doc と合わせる）
- `digests/` と `state/` は自動生成物。手で編集しない。
- テストは実リポジトリの `logs/` や `~/Library/LaunchAgents` に触れない。スクリプトのテストは一時ディレクトリに複製して実行する（`tests/test_scheduled_run.py`）。
