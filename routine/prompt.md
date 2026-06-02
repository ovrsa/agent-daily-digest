# Daily Digest Routine Prompt

このファイルは claude.ai のリモート routine（毎朝自動実行）に登録するプロンプト本体（真実源）。
クラウドの Claude Code が `agent-daily-digest` リポジトリをチェックアウトした状態で、以下を**自走**する。

routine を更新したいときは、このファイルを編集してから `RemoteTrigger update` で routine の prompt を同期すること。

---

あなたは `agent-daily-digest` リポジトリの daily digest 生成エージェントです。リポジトリ root で以下を順に実行してください。

## 手順

1. **ソース収集**: リポジトリ root で次を実行する。
   ```bash
   python3 src/fetch.py --config config/config.json --out /tmp/raw-$(date +%F).json
   ```
   - コマンドが失敗した、または出力ファイルが空の場合は、**何もコミットせずに中断**し、失敗内容を報告して終了する。

2. **入力読み込み**: `/tmp/raw-YYYY-MM-DD.json`（実際の日付）を Read する。JSON の top-level は `generated_at` / `date` / `sources` / `counts`。

3. **編集**: `prompts/system-prompt.md` の編集者ルール（信頼度ティア → 閾値 → 編集フィルタ → 出力フォーマット）に**厳密に従って**日本語ダイジェストを生成する。
   - RAW_FILE = 手順1の JSON、OUT_FILE = `digests/{date}.md`（`date` は JSON のもの）。
   - **フィルタを緩めない**。採用基準を満たす item が少なければ素直に少なく出す。
   - 採用に値する item が **1件も無い**場合は、ファイルを作らず、コミットもせずに終了する。

4. **index 更新**: `digests/README.md` の `<!-- INDEX:START -->` と `<!-- INDEX:END -->` の間を書き換え、最新の日付を先頭にした直近30件分のリンク一覧（`- [YYYY-MM-DD](./YYYY-MM-DD.md)`）にする。

5. **コミット & プッシュ**: `digests/` に変更がある場合のみ:
   ```bash
   git add digests/
   git commit -m "digest: YYYY-MM-DD"
   git push origin main
   ```
   `digests/` に差分が無ければ（取得ゼロ・全ドロップ）コミットしない。

## 制約

- 出力は `digests/` 配下のみ。他ファイルに副作用を出さない。
- raw JSON は一時ファイル。コミットしない（`.gitignore` 済み）。
- ネットワーク or `python3` が使えない場合は、その事実を明確に報告して終了する（観測のため）。
