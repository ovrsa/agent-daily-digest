# agent-daily-digest

Claude Code、Codex、Hermes などの Coding Agent を日々の開発に使う開発者に向けて、毎朝、根拠付きの日本語ダイジェストを1本作る。対象は、新しいモデル、Coding Agent の活用の新しい概念や手法、活用事例、使い方が変わる新機能。価値の低い記事を「読むべき」と推薦しないことを優先し、採用ゼロの日はダイジェストを作らない。

設計の決定は親 Issue [#1](https://github.com/ovrsa/agent-daily-digest/issues/1)（Design Doc）にある。

## 仕組み

ローカル Mac の launchd が毎朝 `scripts/run-local.sh` を起動し、`python -m digest_pipeline` が次の順に進む。

```
collect      config/config.json の collection にあるソースから候補を集める
normalize    本文を取得・抽出し、決定的なゲート（URL・公開日・本文・処理済み・重複）を通す
research     記事ごとに根拠の地図（Evidence Packet）を作る          … Claude（研究）
select       6軸で評価し、採否・区分・掲載文を決める                … Claude（Selector）
render       Markdown を Python で決定的に生成する（冒頭に採用記事の一覧、続けて区分ごとの掲載文）
publish      digests/<日付>.md・index・state/processed.json をコミットし main に push する
judge        別プロンプト・別コンテキストで採否と掲載文を監査する  … Claude（Judge）
comment      Judge のレポートを digest のコミットにコメントする
```

失敗の扱い（Design Doc の Failure policy）:

- 収集・研究・Selector・描画・publish のどこかで失敗したら公開しない。その場合は処理状態も変えないので、同じ記事を次の実行で扱い直す
- 採用ゼロの日はダイジェストを作らない。判断した記事の採否だけを `state: <日付>` としてコミットする
- Judge やコメントが失敗しても、ダイジェストは公開したまま残す。コメントできなかったレポートは `logs/judge/<run_id>.md` に残る

## セットアップ

前提: macOS、Python 3.10 以上、`git` と `gh`（Homebrew）、Claude Code へのログイン。モデルの認証も GitHub の認証も、この Mac に設定済みのものを使う。

```bash
python3 -m venv .venv && .venv/bin/pip install -e .
gh auth status      # push とコミットコメントに使う
```

定期実行は、`main` を checkout した作業ツリーから行う。作業中の変更がある checkout や、別のブランチにいる checkout は使わない。実行のたびに `git pull --ff-only` で最新にしてから始める。

## 手動実行と dry-run

```bash
./scripts/run-local.sh --dry-run                    # 何も公開しない。結果は logs/dry-run/<run_id>/
./scripts/run-local.sh --dry-run --max-articles 5   # 研究する記事を5件までにする（費用の確認用）
./scripts/run-local.sh                              # 本番: main に push し、コミットにコメントする
```

- dry-run でも、ソースの取得とモデルの呼び出しは本番と同じように行う。git と gh は呼ばない。`logs/dry-run/<run_id>/` には、digest、index、処理状態のコピー、コミットメッセージ（`commit.txt`）、コメント本文（`comment.md`）、メトリクスが残る
- 1回の実行で研究する記事は、`config/config.json` の `run.max_articles` の件数まで。ゲートを通った記事から、定点観測のソース（公式ブログ、有識者ブログ、ニュースレター）を先に、発見経路（Hacker News、arXiv のサーベイ論文）を残りの枠で、それぞれ公開日の新しい順に選ぶ。発見経路はキーワード検索なので話題の外れた記事が混ざり、新しさだけで選ぶと定点観測の枠を取るため。`--max-articles N` は、その実行だけ上限を変える
- 上限があるのは、Selector が研究したすべての記事を1回の呼び出しで判断し、その呼び出しに費用と時間の上限があるため。上限を大きくしすぎると Selector が失敗し、何も公開されず、次の実行で同じ記事を研究し直す（理由と実測は `src/digest_pipeline/config.py` の `DEFAULT_MAX_ARTICLES`）
- 選ばれなかった記事は処理状態に記録しない。収集の窓（7日）にある間は次の実行で新しい記事と並べ直し、ほかの記事が上限を埋め続ける間は、研究しないまま窓から外れる。処理状態が空の初回は窓の全体（2026-09-25 の実測でゲート通過58件）が未処理なので、上の順で上限まで研究し、残りは研究しない。研究しなかった件数は、実行の出力の `deferred:` に出る
- 1記事の研究は、5件の実測（2026-09-25）で約 0.075 USD、約29秒
- 終了コードは、ダイジェストの実行が終わった時（Judge やコメントの失敗を含む）に 0、それ以外は 1

## 定期実行（launchd）

```bash
launchd/install.sh --print      # 埋め込み後の plist を表示して検査する。何も登録しない
launchd/install.sh              # ~/Library/LaunchAgents に置いて読み込む
launchd/install.sh --uninstall  # 解除する
```

- 毎日 08:00（ローカル時刻）に実行する。`PATH`、`HOME`、`USER` を plist に明示している（launchd は最小限の環境で起動するため）
- Mac が停止・スリープしていた場合、逃した 08:00 の実行は復帰後に1回だけ行われる。その日のダイジェストは遅れる
- 登録すると、次の 08:00 から main への push とコメントが始まる。登録の前に dry-run で結果を確かめる

## 実行結果の確認

| 何を | どこで |
|---|---|
| ダイジェスト | `digests/<日付>.md`（index は `digests/README.md`） |
| Judge のレポート | ダイジェストのコミットへのコメント。先頭の `run:` が実行 ID |
| 実行ログ | `logs/run-<日付>.log`。スクリプトが起動する前に失敗した時の標準エラーだけは `logs/launchd.log` に残る |
| 実行メトリクス | `logs/metrics/<run_id>.json`（段ごとの状態、記事ごとのゲートと採否、呼び出しごとのトークン・費用・再試行、Judge の指摘の分類） |

`logs/` は Git に入れない。メトリクスと実行ログは35日間残るので、週次レビューの時点で直近1週間分が必ずある。

## 週次レビュー

週に1回、Judge の指摘が妥当だったかを判断して、同じコミットに回答する。

1. 直近のコメントを一覧する

   ```bash
   gh api "repos/{owner}/{repo}/comments?per_page=30" --jq '.[] | "\(.created_at) \(.commit_id[0:7]) \(.html_url)"'
   ```

2. 各レポートの指摘ごとに、ダイジェストと原文を見て、同じコミットへ `ID: 回答` の形で1行ずつ回答する。回答は `妥当` / `一部妥当` / `不当` / `判断不能` のどれか。理由は回答の後に続けてよい

   ```bash
   gh api "repos/{owner}/{repo}/commits/<sha>/comments" -f body='J1: 妥当
   J2: 不当 原文の表に条件が書かれている'
   ```

3. 直近1週間の実行メトリクスを見る（費用、失敗、再試行、指摘の件数）

   ```bash
   .venv/bin/python - <<'PY'
   from datetime import timedelta
   from digest_observe import MetricsStore, render_summary, summarize, utc_now
   for run in MetricsStore().load(since=utc_now() - timedelta(days=7)):
       print(render_summary(summarize(run)))
   PY
   ```

Judge の指摘や人間の回答をもとに、プロンプト・モデル・ソースの設定を自動で変えることはしない。変えるときは人が判断する。

## Git に保存するもの・しないもの

- 保存する: `digests/`（ダイジェストと index）、`state/processed.json`（正規化した URL、初回発見時刻、本文ハッシュ、直近の採否だけ）
- 保存しない: 記事本文、モデルの入出力、Judge の評価（コミットコメントに置く）、実行ログ、メトリクス

## 設定（`config/config.json`）

| ブロック | 内容 |
|---|---|
| `collection` | ソース（種類、取得方法、URL、ON/OFF、件数の上限）、収集の窓（日数。フィードとリリースは公開日、sitemap は最終更新日で判定する）、HTTP 設定 |
| `models` | 研究・Selector・Judge のモデルと、費用の推定に使う定価 |
| `research` | 研究の上限（ラウンド数、追加で読むページ数、時間、文字数） |
| `run` | 1回の実行で研究する記事の件数の上限 |
