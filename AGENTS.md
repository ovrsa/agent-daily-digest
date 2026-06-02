# AGENTS — agent-daily-digest

このリポジトリの薄い repo-local map。詳細はリンク先（真実源）を参照。

## 何をするリポジトリか

毎朝 8 ソースから LLM / Coding Agent 動向を収集し、日本語ダイジェストを生成して `digests/` に main 直コミットする。スケジュールは claude.ai リモート routine。詳細は [`README.md`](./README.md)。

## 真実源

| 関心 | ファイル |
|---|---|
| スケジュール実行の本体プロンプト | [`routine/prompt.md`](./routine/prompt.md) |
| 編集者ルール（要約・フォーマット・信頼度ティア） | [`prompts/system-prompt.md`](./prompts/system-prompt.md) |
| ソース収集ロジック（stdlib のみ） | [`src/fetch.py`](./src/fetch.py) |
| ソース・キーワード・モデル設定 | [`config/config.json`](./config/config.json) |
| ローカル手動実行 | [`scripts/run-local.sh`](./scripts/run-local.sh) |
| 出力アーカイブ | [`digests/`](./digests/) |

## 変更時の注意

- 出力品質を変えたい → `prompts/system-prompt.md`
- 実行フロー（fetch→要約→commit）を変えたい → `routine/prompt.md`（編集後 `RemoteTrigger update` で routine に同期）
- 収集対象を変えたい → `config/config.json`
- `digests/` は自動生成物。手で編集しない。
