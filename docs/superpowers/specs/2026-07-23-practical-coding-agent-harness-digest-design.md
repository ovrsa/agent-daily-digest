# Practical Coding Agent Harness Digest — Design

## Status

Approved in conversation on 2026-07-23.

## Problem

The current digest is framed around “Agentic Coding” and general LLM/Coding
Agent news. That framing has two failure modes:

1. Terminology changes quickly. A fixed keyword set misses practices described
   under new names such as “meta-harness,” “Ralph loop,” or future terms.
2. General model competition, parameter counts, benchmark rankings, and
   open-vs-closed model commentary crowd out operational knowledge.

The digest should help practitioners find concrete ways to organize Coding
Agents into reliable, repeatable systems. It should not be a general AI news
digest.

## Objective

Discover and summarize practical systems that make Coding Agents continue,
coordinate, verify, recover, and finish software-development work.

Selection must be based on operational structure rather than current trend
words. The digest should remain useful if “Agentic Coding” disappears as a
label.

## Primary Reader

A practitioner or technical lead who operates Coding Agents and wants
reusable harness, loop, verification, state-management, and recovery patterns.

## In Scope

- Meta-harnesses, agent harnesses, and orchestrators
- Autonomous and continuous loops
- Planner, executor, reviewer, and verifier role separation
- Subagent dispatch, parallel execution, and handoffs
- Spec → implementation → test → review → repair loops
- Context compression, memory, artifacts, and state transfer
- Verification, evals, review gates, stop conditions, and human approval
- Retry, recovery, timeout, and cost ceilings
- Sandbox, worktree, permission, and credential isolation
- Observability and long-running agent operations
- Concrete `AGENTS.md`, `CLAUDE.md`, hook, prompt, configuration, and
  repository examples
- Adoption results, failures, and quantitative operational evidence
- Survey or review papers that organize the above practices

## Out of Scope

- Model performance competition and benchmark leaderboards
- Parameter-count and training-scale news
- Open-model versus closed-model market competition
- Individual model training or post-training techniques
- Model announcements without direct harness or loop implications
- Consumer AI product features
- Generic claims that AI made development faster
- Articles without implementation, configuration, operational method,
  failure evidence, or measurements
- Research without a concrete relationship to harness or loop operation

## Success Criteria

- A reader can identify a configuration, architecture, workflow, or operating
  principle worth trying.
- Every selected item exposes at least one of: implementation, configuration,
  workflow, termination rule, verification method, failure mode, or
  quantitative result.
- Selection explains how work is operated, not merely what was announced.
- A day with only one or two qualifying items remains a valid digest.
- A day with no qualifying items produces no digest.
- New terminology can be discovered without adding it to a fixed HN keyword
  list first.
- The editor never invents reproducibility details or practical implications
  missing from the raw input.

## Source Policy

The source strategy remains intentionally narrow. The digest is a high-signal
radar, not an exhaustive web-research system.

### Core sources

- Hacker News
- Established author blogs and newsletters already tracked by the repository,
  including Simon Willison, Latent Space, and Interconnects
- arXiv survey, review, and systematization papers

### Supporting source

- Official GitHub Releases for configured Coding Agent tools, but only when a
  release changes harness, loop, subagent, hook, permission, sandbox, review,
  session, or recovery behavior

### Removed from normal collection

- Reddit, due to low evidence density and recurring rate-limit instability
- HF Daily Papers as a general popularity feed; it is replaced by focused
  arXiv survey/review collection

### No automated source expansion

The routine does not perform general web search and does not automatically
follow an item to find more primary sources. It identifies material worth
manual investigation by the reader.

## Collection Design

### Hacker News

Replace fixed-keyword discovery with broad collection of recent stories:

- Fetch stories from the previous 24 hours.
- Use a discovery floor around 30 points to control volume.
- Do not require `agent`, `harness`, or any current trend term in the title.
- Preserve title, URL, publication time, points, comment count when available,
  and story text.

Popularity is not the final relevance decision:

- Stories with at least 150 points are normal editorial candidates.
- Stories from 30 to 149 points remain eligible only when the raw item exposes
  concrete evidence such as a repository, configuration, code, measurements,
  or a detailed operating account.

This exception prevents niche but reproducible harness work from being removed
solely because it has not yet become popular.

### Established blogs and newsletters

Continue broad feed collection. Do not prefilter these feeds with a volatile
trend vocabulary. The editorial gates decide relevance.

Record source freshness. If a feed only returns stale entries, report that
condition in collection statistics and do not treat those entries as current
news.

### arXiv surveys

Collect recent papers whose metadata indicates survey, review, or
systematization work in software engineering, Coding Agents, LLM agents, and
multi-agent systems.

Survey-like wording is a discovery signal, not automatic acceptance. The paper
must organize knowledge relevant to harness design or operation. Individual
model capability papers remain out of scope.

### GitHub Releases

Continue polling configured repositories. Editorial selection keeps only
changes with direct operational impact on Coding Agent systems. Cosmetic UI
changes and releases with no usable change description are dropped.

## Raw Data Contract

The existing normalized item fields remain:

- `source`
- `title`
- `url`
- `published`
- `score`
- `summary_hint`

Add source-level health metadata:

- fetch status
- fetched-at time
- newest item timestamp
- error or stale reason when applicable

Add source-specific evidence when available:

- HN comment count
- HN story text
- arXiv abstract and categories
- release body

The fetcher gathers and normalizes facts. It does not assign practical
relevance or generate trend labels.

## Editorial Pipeline

The editor applies the following stages in order.

### 1. Credibility gate

Retain the existing tier concept:

- Tier S: official releases and published papers
- Tier A: accountable established authors and publications
- Tier B: community items backed by verifiable artifacts
- Tier C: unsupported opinion, anecdotes, marketing, and speculation

Tier C is dropped.

### 2. Practicality gate

An item must explicitly expose at least one of:

- workflow or architecture
- implementation, repository, or configuration
- loop start or stop condition
- agent role separation
- verification or review method
- context or state transfer
- retry or failure recovery
- cost, success rate, duration, or another operational measurement
- production failure and mitigation

An item that merely names a relevant topic does not pass.

### 3. Evidence gate

- The source or author must be identifiable.
- The raw title and summary must contain enough detail to support the output.
- A title-only item is dropped even if it sounds important.
- The editor may not infer a workflow, lesson, or implication not present in
  the raw data.
- Missing output fields are omitted rather than filled with speculation.

### 4. Structural classification

Classify selected items by durable operational structure:

- `case-study`
- `orchestration`
- `loop`
- `context-state`
- `verification`
- `isolation-control`
- `operations`
- `survey`

These labels describe mechanisms. They are not discovery keywords.

### 5. Deduplication and ranking

- Merge items that describe the same mechanism or event.
- Prefer the source with stronger evidence and credibility.
- Rank concrete operational cases above surveys, releases, and commentary.
- Do not meet a target count by weakening the gates.
- Cap the output at approximately ten items.

## Output Design

Use operational sections instead of model/news/source-oriented sections:

```markdown
# Coding Agent Harness Digest — YYYY-MM-DD

## TL;DR
## 🧪 実運用ケース
## 🔁 Loop・Orchestration
## 🧠 Context・State Management
## ✅ Verification・Guardrails
## 📚 Survey・体系化
## 🔧 実務に効くRelease
## 🧭 Emerging Vocabulary
## 📊 収集・選別統計
```

Empty sections are omitted.

Each item may contain:

- what is being operated
- loop or harness structure
- reusable elements
- termination and verification
- constraints and failures
- evidence
- credibility tier

Only fields supported by the raw input are emitted.

### Emerging Vocabulary

List unfamiliar terms that appeared in accepted items and briefly describe how
the source used them. These terms are observations, not automatic search-query
expansion.

This section makes terminology drift visible without allowing trend language
to control selection.

### Statistics

Report:

- per-source fetched counts
- source errors and stale feeds
- counts after the practicality and evidence gates
- final selected count
- credibility-tier counts
- main exclusion reasons

## Error Handling

- A single source failure does not stop the run.
- A fetch command failure or empty raw file stops the run without a digest.
- Stale feeds are reported and contribute no current items.
- arXiv, HN, or blog parse errors are reported independently.
- Zero accepted items produces no digest or commit.
- Raw files remain temporary and are never committed.

## Configuration Changes

Update `config/config.json` to:

- remove the fixed HN keyword list
- configure the HN time window and discovery score floor
- configure arXiv categories and survey-discovery terms
- disable or remove normal Reddit and HF Daily Papers collection
- retain configured blog feeds and Coding Agent GitHub repositories

Durable operational categories belong in the editorial prompt, not config,
because they define editorial intent rather than transport configuration.

## Repository Changes

- `src/fetch.py`
  - broad recent HN collection
  - arXiv survey collector
  - source-health metadata
  - removal of normal Reddit and HF Daily Papers collection
- `config/config.json`
  - new HN and arXiv transport settings
- `prompts/system-prompt.md`
  - new scope, gates, classifications, and output format
- `routine/prompt.md`
  - synchronize the new collection and editorial flow
- `README.md`
  - describe the revised purpose and sources
- `AGENTS.md`
  - update the repo-local map where source responsibilities change
- fetcher tests
  - standard-library fixture-based tests for collection and normalization

Generated files under `digests/` are not manually edited as part of this
change.

## Verification Strategy

### Fetcher tests

- HN collection does not require configured topic keywords.
- HN stories below the discovery floor are excluded.
- HN normalized data preserves score and evidence fields.
- arXiv survey-like papers are collected and normalized.
- non-survey arXiv papers are excluded from this collector.
- stale and failed sources produce health metadata without aborting other
  sources.

### Editorial fixtures

Use representative raw fixtures to verify:

- an article with an unknown term but a concrete loop is accepted
- a model benchmark or parameter-race article is rejected
- a title-only harness article is rejected
- a low-score HN item with a concrete repository remains eligible
- a generic low-score HN opinion is rejected
- a survey that organizes harness practice is accepted
- an unrelated model survey is rejected
- absent facts are not invented in the digest
- one accepted item produces a valid short digest
- zero accepted items produces no output

### End-to-end check

Run the local flow against a captured fixture, validate the Markdown structure,
and confirm that only the expected digest file changes.

## Acceptance Criteria

1. An HN item can be discovered without matching a fixed topic keyword.
2. A new term can be accepted based on loop or harness structure.
3. Model competition, parameter counts, and benchmark-race items are rejected.
4. Title-only items are rejected.
5. Concrete lower-score HN items remain eligible under the evidence exception.
6. arXiv collection favors relevant survey/review papers over individual model
   papers.
7. Output contains no claims unsupported by the raw input.
8. Source failure and staleness are visible while other sources continue.
9. A one-item digest is valid; a zero-item digest is not written.
10. No generated digest is manually edited during implementation.

## Deferred Work

- General web search
- Automatic primary-source follow-up
- Persistent trend-term expansion
- Cross-day embeddings or semantic indexes
- Personalized ranking per reader

These may be reconsidered only if broad HN collection and structural editorial
selection still miss important practical work.
