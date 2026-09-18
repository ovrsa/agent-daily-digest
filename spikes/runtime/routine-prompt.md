# routine プロンプト（Issue #2 の remote 実測）

1回限りの routine（`run_once_at`）のプロンプトとして、この節の本文をそのまま渡す。
routine の作成には `job_config.ccr.environment_id` か `job_config.ccr.self_hosted_runner_pool_id` が要る。

**このファイルがリポジトリに入る前に試す場合は、`probe.sh` / `sdk_probe.py` / `schema_probe.py` の
全文をプロンプトに貼り、手順1を「3ファイルを `/tmp/spike2/` に書く」に置き換える。**
routine はリポジトリを clone するので、マージ後であればスクリプトは checkout に含まれる。

---

Issue #2 (ovrsa/agent-daily-digest) の Spike の実測です。scheduled job の実行環境と GitHub 権限を1回の実行で観測します。

## 守ること

- 秘密情報（トークン、API キー、環境変数の値）を出力にも投稿にも載せない。環境変数は名前と状態だけ
- リポジトリのファイルを変更しない。コミットしない。実際の push をしない（`--dry-run` だけ）
- ツールの実行が拒否されたり承認待ちになったら、その拒否文をそのまま記録して次へ進む。再試行しない
- 書き込みは `/tmp/spike2/` だけ

## 手順

1. `mkdir -p /tmp/spike2 && cp spikes/runtime/*.py /tmp/spike2/`
2. `PROBE_WORKDIR=/tmp/spike2 bash spikes/runtime/probe.sh 2>&1 | tee /tmp/spike2/probe.log` を実行する。20分ほどかかる。タイムアウトは長めに取る
3. `git push --dry-run origin HEAD:refs/heads/main 2>&1 | sed -E 's#//[^@/]*@#//<redacted>@#' | tee /tmp/spike2/push.log` を実行する。拒否されたらその文面を記録する。**実際の push はしない**
4. `/tmp/spike2/probe.log` を読み、秘密情報が混ざっていないか確認する。混ざっていたら伏せる
5. 対象コミット（`git rev-parse origin/main` の値。以下 `<SHA>`）へ commit comment を1件投稿する。方式1は `gh api`、失敗したら方式2の `curl`

```bash
gh api -X POST "repos/ovrsa/agent-daily-digest/commits/<SHA>/comments" \
  -f body="$(cat /tmp/spike2/comment.md)" -q '{id: .id, url: .html_url, user: .user.login}'
```

`/tmp/spike2/comment.md` の構成:

- 1行目: `[spike #2] scheduled job (remote routine) 実行環境の実測。Refs #2`
- 箇条書きで `posted_at`（UTC ISO8601）と、分かれば `session`（このセッションの claude.ai URL）
- 見出し `## metrics retention sample` と、その下に json のコードブロックで次のダミー値（実行メトリクスを commit comment に保持できるかの検証）:
  `{"run_id":"spike-2","status":"succeeded","stages":[{"stage":"collect","status":"succeeded","ms":1200}],"llm_calls":[{"call_id":"selector-1","role":"selector","model":"claude-sonnet-5","attempts":[{"attempt_number":1,"status":"succeeded","usage":{"input_tokens":12000,"output_tokens":900},"cost":{"usd":0.05,"basis":"estimated"}}]}],"published_must_read_count":0}`
- 見出し `## probe.log` と、その下に `<pre>` で囲んだ `probe.log` の全文
- 見出し `## push.log` と、その下に `<pre>` で囲んだ `push.log` の全文

**要約しない。** この comment が実行結果の保持先そのもので、5項目の観測記録になる。
合計 60000 文字を超えるときだけ `5-outbound` / `1-2-sdk` / `3-schema-and-errors` の節を優先して残し、末尾に `truncated` と書く。

6. 投稿が成功したら `gh api "repos/ovrsa/agent-daily-digest/commits/<SHA>/comments" -q 'length'` で件数を確認する
7. 最終メッセージに次を書く。**commit comment の投稿が失敗した場合は、`probe.log` の全文を最終メッセージに貼る**

- commit comment の URL と HTTP の結果（201 か、失敗ならステータスと本文）
- `push --dry-run` の結果（通ったか、拒否理由）
- `probe.log` の各節の要点: OS と Python のバージョン、依存導入の可否と所要秒数、17 URL のうち 200 以外だったもの、SDK 呼び出しの `apiKeySource` と `subtype` / `is_error` / `num_turns` / `structured_output`、スキーマ制約の遵守結果、例外種別
- 拒否されたツール操作があればその文面
