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
follow links found inside an article to discover more primary sources.

Fetching the canonical page directly referenced by an HN story or configured
feed item is permitted and is considered part of collecting that item, not
source expansion. The collector extracts up to 12,000 characters of readable
text from that one canonical page. It does not follow citations, related links,
repository links, or other outbound links from the page. If canonical-page
retrieval fails, the item remains eligible only when the HN self-post, feed
content, arXiv abstract, or release body already contains enough evidence to
pass the editorial gates.

The digest identifies material worth deeper manual investigation by the reader.

## Collection Design

### Hacker News

Replace fixed-keyword discovery with broad collection of recent stories:

- Fetch stories published in the previous 24 hours.
- Use an inclusive discovery floor of 30 points to control volume.
- Fetch at most the 50 highest-scoring eligible stories.
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

For an external HN story, fetch the story's canonical URL once and store the
bounded extracted text with provenance `canonical_page`. For an Ask HN or other
self-post, use the HN story text with provenance `hn_self_post`.

### Established blogs and newsletters

Continue broad feed collection. Do not prefilter these feeds with a volatile
trend vocabulary. The editorial gates decide relevance.

Record source freshness. If a feed only returns stale entries, report that
condition in collection statistics and do not treat those entries as current
news.

Blog and newsletter items are eligible for 72 hours after publication. The
collector may fetch each eligible item's canonical page once under the bounded
canonical-page rule above.

### arXiv surveys

Collect recent papers whose metadata indicates survey, review, or
systematization work in software engineering, Coding Agents, LLM agents, and
multi-agent systems.

Survey-like wording is a discovery signal, not automatic acceptance. The paper
must organize knowledge relevant to harness design or operation. Individual
model capability papers remain out of scope.

The default arXiv eligibility window is 30 calendar days. Collection is limited
to `cs.SE`, `cs.AI`, `cs.MA`, and `cs.CL`. `survey`, `review`, or
`systematization` must appear in the title or abstract, case-insensitively, for
the collector to emit a candidate. Editorial selection then applies the
practicality and evidence gates to the abstract.

### GitHub Releases

Continue polling configured repositories. Editorial selection keeps only
changes with direct operational impact on Coding Agent systems. Cosmetic UI
changes and releases with no usable change description are dropped.

Releases are eligible for seven calendar days after publication.

### Time and repeat-item rules

- All eligibility calculations use UTC.
- `published_at` is the source-provided publication timestamp; an updated
  timestamp is used only when no publication timestamp exists.
- Items with neither a valid publication nor updated timestamp are excluded
  from the daily candidate set and counted as `missing_date`.
- Within one run, canonicalized URL is the primary deduplication key; source ID
  plus normalized title is the fallback.
- Before selection, the editor reads links from the previous 14 digest files.
  A canonical URL already published in that window is excluded as
  `recent_duplicate`, except for a distinct versioned release URL.
- A source is `stale` when retrieval succeeds but its newest parseable item is
  more than 14 calendar days old. A healthy source with no items inside the
  eligibility window is `success`, not `stale`.

## Raw Data Contract

The fetcher writes a versioned JSON envelope:

```json
{
  "schema_version": 2,
  "generated_at": "2026-07-23T10:58:42Z",
  "window": {
    "timezone": "UTC",
    "start": "2026-07-22T10:58:42Z",
    "end": "2026-07-23T10:58:42Z"
  },
  "items": [],
  "source_health": []
}
```

Each `items` entry has:

```json
{
  "id": "hackernews:49008211",
  "source_id": "hackernews",
  "source_type": "community",
  "title": "string",
  "url": "https://example.com/item",
  "author": "string or null",
  "publisher": "string or null",
  "published_at": "RFC3339 timestamp",
  "updated_at": "RFC3339 timestamp or null",
  "score": 859,
  "comment_count": 120,
  "summary_hint": "string",
  "evidence_text": "bounded source text",
  "evidence_provenance": "canonical_page",
  "metadata": {}
}
```

Required fields are `id`, `source_id`, `source_type`, `title`, `url`,
`published_at`, `summary_hint`, `evidence_text`, `evidence_provenance`, and
`metadata`. Nullable fields are `author`, `publisher`, `updated_at`, `score`,
and `comment_count`.

`evidence_provenance` is one of:

- `canonical_page`
- `hn_self_post`
- `feed_content`
- `arxiv_abstract`
- `release_body`

`summary_hint` is always derived from the same captured source represented by
`evidence_provenance`; it is never model-generated by the fetcher.

Each `source_health` entry has:

```json
{
  "source_id": "hackernews",
  "status": "success",
  "fetched_at": "2026-07-23T10:58:42Z",
  "item_count": 25,
  "eligible_count": 20,
  "newest_item_at": "2026-07-23T10:40:00Z",
  "reason": null
}
```

`status` is one of `success`, `partial`, `stale`, `failed`, or `disabled`.
`newest_item_at` and `reason` are nullable. `partial` means the source itself
was parsed but one or more canonical-page fetches or entries failed.

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

Every selected item receives exactly one primary category and zero or more
secondary labels. Primary categories map to output sections as follows:

| Primary category | Output section |
|---|---|
| `case-study` | `🧪 実運用ケース` |
| `orchestration`, `loop` | `🔁 Loop・Orchestration` |
| `context-state` | `🧠 Context・State Management` |
| `verification`, `isolation-control` | `✅ Verification・Guardrails` |
| `operations` | `🛟 Operations・Recovery` |
| `survey` | `📚 Survey・体系化` |

Source type does not control placement. A GitHub Release is placed in the
section matching its operational primary category and labeled as a release.
When duplicate items are merged, the output preserves the primary URL plus a
list of all supporting source IDs and URLs.

### 5. Deduplication and ranking

- Merge items that describe the same mechanism or event.
- Prefer the source with stronger evidence and credibility.
- Rank concrete operational cases above surveys, releases, and commentary.
- Do not meet a target count by weakening the gates.
- Cap the output at ten items. If more than ten pass, select the ten with the
  highest deterministic ranking tuple:
  1. evidence strength, descending
  2. credibility tier (`S`, then `A`, then `B`)
  3. primary-category priority (`case-study`, `loop`, `orchestration`,
     `verification`, `isolation-control`, `operations`, `context-state`,
     `survey`)
  4. publication timestamp, descending
  5. stable item ID, ascending

Evidence strength is an integer:

- `3`: concrete artifact or configuration plus an explained workflow,
  verification method, failure, or measurement
- `2`: detailed workflow plus at least one verification method, failure, or
  measurement, without a directly available artifact
- `1`: one concrete operational mechanism supported by source text
- `0`: insufficient evidence; the item fails the evidence gate

## Output Design

Use operational sections instead of model/news/source-oriented sections:

```markdown
# Coding Agent Harness Digest — YYYY-MM-DD

## TL;DR
## 🧪 実運用ケース
## 🔁 Loop・Orchestration
## 🧠 Context・State Management
## ✅ Verification・Guardrails
## 🛟 Operations・Recovery
## 📚 Survey・体系化
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
- Per-source failures and canonical-page failures are recoverable and produce
  `failed` or `partial` source-health records.
- The fetch command exits nonzero only when configuration cannot be read, no
  collector can be initialized, the output cannot be written, or the JSON
  envelope fails schema validation.
- A valid schema-v2 envelope with zero items exits successfully.
- A nonzero fetch exit, missing file, zero-byte file, or invalid envelope stops
  the run without a digest.
- Stale feeds are reported and contribute no current items.
- arXiv, HN, or blog parse errors are reported independently.
- Zero accepted items produces no digest or commit.
- Raw files remain temporary and are never committed.
- Source health and editorial counts are printed into the routine execution
  log. When a digest is produced they also appear in its statistics section.
  When no digest is produced, the routine log is the observable record.

## Configuration Changes

Update `config/config.json` to:

- remove the fixed HN keyword list
- configure the exact defaults: HN 24-hour window, inclusive 30-point floor,
  50-story cap, blog 72-hour window, release seven-day window, arXiv 30-day
  window, and 14-day source-staleness threshold
- configure arXiv categories and the case-insensitive discovery terms
  `survey`, `review`, and `systematization`; default categories are `cs.SE`,
  `cs.AI`, `cs.MA`, and `cs.CL`
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
- `src/render.py`
  - validate the model-produced editorial manifest
  - render validated Markdown deterministically
- `routine/prompt.md`
  - synchronize the new collection, manifest, validation, and rendering flow
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
- Boundary fixtures cover 29/30 and 149/150 points.
- HN normalized data preserves score and evidence fields.
- arXiv survey-like papers are collected and normalized.
- non-survey arXiv papers are excluded from this collector.
- Date-boundary fixtures cover each source window and UTC conversion.
- stale and failed sources produce health metadata without aborting other
  sources.

### Editorial fixtures

The editor produces a temporary JSON editorial manifest rather than writing
Markdown directly:

```json
{
  "selected": [
    {
      "item_ids": ["hackernews:49008211"],
      "primary_category": "case-study",
      "secondary_labels": ["loop"],
      "fields": {
        "what": {
          "text": "Japanese summary",
          "evidence": ["exact substring from evidence_text"]
        }
      }
    }
  ],
  "rejected": [
    {
      "item_id": "arxiv:0000.00000",
      "reason": "model_competition"
    }
  ]
}
```

Each factual output field must cite one or more exact substrings from the raw
item's `evidence_text`. `src/render.py` rejects unknown item IDs, invalid
categories, missing evidence, evidence strings not found in the corresponding
raw item, more than ten selected items, and malformed manifests. It then
renders Markdown deterministically.

Every raw item must appear exactly once in either `selected` or `rejected`.
Allowed rejection reason codes are `tier_c`, `outside_scope`,
`model_competition`, `insufficient_practical_detail`, `insufficient_evidence`,
`score_threshold`, `missing_date`, `recent_duplicate`, and `merged_duplicate`.
Selected items include `credibility_tier` and integer `evidence_strength`, so
the renderer can validate and apply the ranking tuple.

Use representative raw fixtures and checked-in expected manifests to verify:

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

Automated tests assert exact selected IDs, rejection reason codes, primary
categories, evidence-substring validity, and rendered section placement. Model
quality itself is evaluated manually against the same fixtures with a rubric:

- every claim is entailed by its cited evidence
- every accepted item passes the practicality gate
- every rejected model-competition fixture remains rejected
- no absent operational field is synthesized

Because semantic entailment cannot be proven by Markdown structure checks, it
is explicitly a manual evaluation gate before prompt changes are accepted.

### End-to-end check

Run the local flow against a captured fixture, validate the Markdown structure,
and confirm that only the expected digest file changes.

## Acceptance Criteria

1. An HN item can be discovered without matching a fixed topic keyword.
2. A new term can be accepted based on loop or harness structure.
3. Model competition, parameter counts, and benchmark-race items are rejected.
4. Title-only items are rejected.
5. Concrete lower-score HN items remain eligible under the evidence exception.
6. The arXiv collector emits only in-window papers in configured categories
   whose title or abstract contains a configured survey term; the editor
   rejects those that fail the practicality gate.
7. Output contains no claims unsupported by the raw input.
8. Source failure and staleness are visible while other sources continue.
9. A one-item digest is valid; a zero-item digest is not written.
10. No generated digest is manually edited during implementation.
11. Eleven passing candidates are deterministically reduced to ten.
12. Previously published canonical URLs are suppressed for 14 digest days,
    except for distinct versioned release URLs.

## Deferred Work

- General web search
- Automatic primary-source follow-up
- Persistent trend-term expansion
- Cross-day embeddings or semantic indexes
- Personalized ranking per reader

These may be reconsidered only if broad HN collection and structural editorial
selection still miss important practical work.
