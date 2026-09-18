# spikes/runtime

Issue #2（定期実行環境と GitHub 権限の検証）で使った1回きりのプローブ。
**パイプラインの一部ではない。** 再実測が必要になったときだけ使う。

## 中身

| ファイル | 役割 |
|---|---|
| `probe.sh` | 実行環境を1回で観測する。OS、Python、依存導入、17 URL への GET、SDK 呼び出し、永続領域 |
| `sdk_probe.py` | Agent SDK の最小呼び出し。`init.apiKeySource` と `ResultMessage` のメトリクスを出す |
| `schema_probe.py` | `SelectorOutput` の JSON Schema をそのまま構造化出力に渡し、制約が守られるかと SDK の例外種別を出す |
| `pattern_probe.py` | `pattern` / `minItems` / `anyOf(null)` の強制を個別に確かめる |

## 守っていること

- 秘密情報の値を出力しない。環境変数は名前と状態（`unset` / `placeholder` / `set`）だけ
- `git remote` の URL は認証部分を伏せる
- 書き込みは `/tmp/spike2/`（`PROBE_WORKDIR` で変更可）だけ。リポジトリを変更しない

## ローカルで動かす

```bash
uv venv -q /tmp/spike2/venv
uv pip install -q --python /tmp/spike2/venv/bin/python claude-agent-sdk pydantic pytest
cp spikes/runtime/*.py /tmp/spike2/
PROBE_WORKDIR=/tmp/spike2 bash spikes/runtime/probe.sh 2>&1 | tee /tmp/spike2/probe.log
```

macOS には `timeout` が無いので、`probe.sh` の SDK 呼び出しは `coreutils` の `gtimeout` を
`timeout` という名前で PATH に置くか、その行を直接実行する。

`schema_probe.py` は `digest_contracts` を読むので `PYTHONPATH=src` が要る。

LLM を呼ぶので実行ごとに課金される。2026-09-18 の実測では `schema_probe.py` 1回で 0.17 USD、
`pattern_probe.py` 1回で 0.03〜0.11 USD だった。

## launchd 相当の最小環境で動かす

定期実行は launchd から起動する（#1 の Change log、2026-09-18 に remote routine から変更）。
launchd は対話シェルより環境変数が少ないので、`env -i` で近似して確かめる。

```bash
env -i HOME="$HOME" PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin USER="$USER" \
  /tmp/spike2/venv/bin/python spikes/runtime/sdk_probe.py claude-sonnet-5
```

**`USER` を外すと Claude Code は `Not logged in · Please run /login` で終了する。**
`LOGNAME` では代替できない。`gh` と git の認証取得は `USER` に依存しない。
plist の `EnvironmentVariables` には `PATH` と `USER` の両方を書く。

remote routine 経路は使わない。この検証で作った routine プロンプトは削除した。
