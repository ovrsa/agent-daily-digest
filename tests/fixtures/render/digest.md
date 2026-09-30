---
date: 2026-09-18
type: agent-daily-digest
---

# Agent / Coding Agent Daily Digest 2026-09-18

## 今日の一覧

1. **Coding Agent / Must Read** [Splitting plan and act into separate agent sessions](https://example.com/posts/plan-act-split)  
   計画と実行を別セッションに分けた200件の比較
2. **Coding Agent / Must Read** [Per-tool permission scopes \[v0.9\]](https://example.com/releases/tool-scopes-v0-9)  
   ツールごとの権限スコープの追加
3. **Coding Agent / Must Read** [Measuring retry cost in a long agent loop](https://example.com/posts/retry-cost)  
   再試行1回あたりのトークン数の実測
4. **Coding Agent / Worth Knowing** [Prompt cache hit rates in production](https://example.com/posts/cache-hit-rates)  
   プロンプトキャッシュのヒット率の1週間の記録
5. **Coding Agent / Worth Knowing** [Release notes (2026-09-16)](<https://example.com/releases/(2026-09-16)>)  
   リポジトリマップ生成のインクリメンタル化

## Coding Agent

### Must Read

#### 1. [Splitting plan and act into separate agent sessions](https://example.com/posts/plan-act-split)

ソース: anthropic_engineering / 著者: Alice Kim / 公開: 2026-09-17

**何をしたか／何が分かったか**

開発チームが計画と実行を別セッションに分け、200件のタスクで失敗率を比較した。

**読む理由**

長いエージェントループを運用する読者は、同じ分割を自分のハーネスと比較できる。

**根拠**

記事に設定値と、200件を対象にした変更前後の失敗率の表がある。

**留保**

比較対象は社内の1つのタスクセットに限られる。

#### 2. [Per-tool permission scopes \[v0.9\]](https://example.com/releases/tool-scopes-v0-9)

ソース: github_releases / 公開: 2026-09-16

**何をしたか／何が分かったか**

v0.9 がツールごとの権限スコープを追加した。

**読む理由**

ハーネス運用者はラッパースクリプトを書かずにツール権限を絞れる。

**根拠**

リリースノートが新しい設定キーと既定値を列挙している。

#### 3. [Measuring retry cost in a long agent loop](https://example.com/posts/retry-cost)

ソース: simonw / 著者: Simon Willison / 公開: 2026-09-17

**何をしたか／何が分かったか**

著者が再試行1回あたりの入力トークン数を計測し、ループ全体のコストに占める割合を示した。

**読む理由**

再試行の上限を決める根拠を、勘ではなく実測値に置き換えられる。

**根拠**

計測スクリプトと、3モデル分のトークン数の表を掲載している。

**留保**

モデル価格は記事公開時点のもので、更新の有無は本文にない。

### Worth Knowing

#### 4. [Prompt cache hit rates in production](https://example.com/posts/cache-hit-rates)

ソース: latent_space / 公開: 2026-09-15

**何をしたか／何が分かったか**

運用チームがプロンプトキャッシュのヒット率を1週間記録し、プレフィックス長との関係を示した。

**読む理由**

キャッシュ前提のプロンプト設計を、実測のヒット率から見直せる。

**根拠**

日次のヒット率と、プレフィックス長を変えた3条件の比較がある。

#### 5. [Release notes (2026-09-16)](<https://example.com/releases/(2026-09-16)>)

ソース: github_releases / 著者: Paul Gauthier / 公開: 2026-09-16

**何をしたか／何が分かったか**

2026-09-16 のリリースがリポジトリマップの生成をインクリメンタルに変更した。

**読む理由**

大きなリポジトリで起動時間が問題になっている場合、更新するかを判断できる。

**根拠**

リリースノートに生成時間の変更前後の数値がある。

**留保**

数値は著者の環境で計測したもの。

## Hermes系Agentの活用事例

本日の採用記事はありません。
