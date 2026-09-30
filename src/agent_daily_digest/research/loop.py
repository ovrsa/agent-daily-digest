"""The bounded research loop.

```
SourceDocument -> Evidence Map -> references valid? -- no --> extraction_failed
                                        | yes
                                   sufficient? -- yes --> complete
                                        | no
                     budget left and a question with a target?
                        | no                          | yes
             budget_exhausted /           re-read the named paragraphs, or open a
             no_open_questions            page the article links to (depth 1)
                                          -> next round of the Evidence Map
```

Every limit comes from `ResearchBudget` and is enforced here. The model
proposes questions, paragraphs and URLs; this module decides which of them are
acted on. A URL is opened only when the article's own body links to it, it is
not an article of this run, it is not in the processing state, and the page
cap is not reached. A page whose body hash matches something already read is
discarded. Research runs once per cluster, on the representative.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from urllib.parse import urljoin

from agent_daily_digest.contracts import (
    ErrorRecord,
    EvidencePacket,
    FetchedReference,
    LLMCallMetrics,
    LLMRole,
    NormalizedArticle,
    ResearchBudget,
    ResearchStatus,
    ResearchStopReason,
    ResearchTrace,
    SourceDocument,
    compute_content_hash,
)
from agent_daily_digest.llm.call import StructuredRequest
from agent_daily_digest.normalize import MIN_PRIMARY_INFO_CHARS
from agent_daily_digest.content.fetch import BodyFetcher, FetchedPage
from agent_daily_digest.state import ProcessedIndex
from agent_daily_digest.content.urls import UrlRejected, canonicalize_url
from agent_daily_digest.content.extract import extract_document
from agent_daily_digest.llm.call import CallOutcome, CallSpec, LLMResponse, RetryPolicy, measured_call
from agent_daily_digest.llm.pricing import PricingTable

from .clusters import Cluster, cluster_articles
from .documents import render_article, render_reference, source_document
from .extraction import Assessment, EvidenceMap, ExtractionOutput, QuestionDraft, accept, assess
from .library import SourceLibrary, render_map
from .prompt import PROMPT_VERSION, SYSTEM_PROMPT, round_one_prompt, round_two_prompt

Invoker = Callable[[StructuredRequest], LLMResponse]

DEFAULT_RETRY = RetryPolicy(max_attempts=2)
"""One retry per round. A map that fails the reference checks twice is an extraction failure."""


@dataclass(frozen=True)
class ResearchInput:
    article: NormalizedArticle
    links: tuple[str, ...] = ()
    """`ExtractedDocument.links` of the article's page: raw `href` values."""


@dataclass(frozen=True)
class ResearchResult:
    packets: tuple[EvidencePacket, ...]
    library: SourceLibrary

    def packet(self, article_id: str) -> EvidencePacket:
        for packet in self.packets:
            if packet.article_id == article_id:
                return packet
        raise KeyError(article_id)


def resolve_links(article: NormalizedArticle, hrefs: Sequence[str]) -> tuple[str, ...]:
    """Canonical http(s) URLs the article links to, excluding the article itself."""
    resolved: list[str] = []
    for href in hrefs:
        try:
            url = canonicalize_url(urljoin(article.canonical_url, href))
        except UrlRejected:
            continue
        if url != article.canonical_url and url not in resolved:
            resolved.append(url)
    return tuple(resolved)


@dataclass
class Researcher:
    invoke: Invoker
    fetch: BodyFetcher
    pricing: PricingTable
    model: str
    budget: ResearchBudget = field(default_factory=ResearchBudget)
    processed: ProcessedIndex | None = None
    policy: RetryPolicy = DEFAULT_RETRY
    record: Callable[[LLMCallMetrics], None] | None = None
    max_call_usd: float | None = 0.5
    """Passed to the CLI for each call, which stops a call whose list-price cost passes it."""
    monotonic: Callable[[], float] = time.monotonic

    def run(self, inputs: Sequence[ResearchInput]) -> ResearchResult:
        articles = [item.article for item in inputs]
        links = {item.article.article_id: resolve_links(item.article, item.links) for item in inputs}
        library = SourceLibrary()
        seen = _Seen(
            urls={article.canonical_url for article in articles},
            hashes={article.content_hash for article in articles},
        )
        packets: list[EvidencePacket] = []
        for cluster in cluster_articles(articles, links):
            packets.append(self._research(cluster, links[cluster.representative.article_id], library, seen))
            packets += [_supporting_packet(member, cluster.representative) for member in cluster.supporting]
        order = {article.article_id: index for index, article in enumerate(articles)}
        return ResearchResult(packets=tuple(sorted(packets, key=lambda p: order[p.article_id])), library=library)

    # -- one cluster ------------------------------------------------------
    def _research(self, cluster: Cluster, links: tuple[str, ...], library: SourceLibrary, seen: _Seen) -> EvidencePacket:
        article = cluster.representative
        started = self.monotonic()
        state = _State(article=article, cluster=cluster, links=links if self.budget.max_link_depth >= 1 else ())
        main = source_document(article.article_id, "main", article.canonical_url, article.body_text)
        library.add(main)
        state.documents["main"] = main

        block, omitted = render_article(article, main, state.links, max_chars=self.budget.max_source_chars)
        if omitted:
            state.unresolved.append(f"文字数の上限で本文の {len(omitted)} 段落を読んでいない（{', '.join(omitted)}）")
        outcome = self._round(state, round_one_prompt(block, omitted), prior=None)
        if not outcome.succeeded:
            return state.packet(_failed(outcome.error), ResearchStopReason.EXTRACTION_FAILED, self._elapsed(started))
        state.map = outcome.value

        while True:
            assessment = assess(article, state.map)
            if assessment.status is ResearchStatus.COMPLETE:
                return state.packet(assessment, ResearchStopReason.SUFFICIENT, self._elapsed(started))
            if not state.map.questions:
                return state.packet(assessment, ResearchStopReason.NO_OPEN_QUESTIONS, self._elapsed(started))
            # The question cap counts across rounds, like the round and page caps.
            remaining = self.budget.max_open_questions - state.questions_considered
            if (
                remaining <= 0
                or state.rounds >= self.budget.max_rounds
                or self._elapsed(started) >= self.budget.max_seconds * 1000
            ):
                return state.packet(assessment, ResearchStopReason.BUDGET_EXHAUSTED, self._elapsed(started))
            questions = state.map.questions[:remaining]
            state.questions_considered += len(questions)

            asked, blocks, capped = self._gather(state, questions, library, seen, started)
            if not blocks:
                reason = ResearchStopReason.BUDGET_EXHAUSTED if capped else ResearchStopReason.NO_OPEN_QUESTIONS
                return state.packet(assessment, reason, self._elapsed(started))
            outcome = self._round(state, round_two_prompt(render_map(state.map), asked, blocks), prior=state.map)
            if not outcome.succeeded:
                state.unresolved.append(f"追加の確認で根拠を抽出できなかった（{outcome.error.kind.value}）")
                return state.packet(assess(article, state.map), ResearchStopReason.EXTRACTION_FAILED, self._elapsed(started))
            state.map = outcome.value

    def _round(self, state: _State, prompt: str, *, prior: EvidenceMap | None) -> CallOutcome[EvidenceMap]:
        state.rounds += 1
        request = StructuredRequest(
            model=self.model,
            system_prompt=SYSTEM_PROMPT,
            prompt=prompt,
            schema=ExtractionOutput.model_json_schema(),
            max_budget_usd=self.max_call_usd,
        )
        documents = dict(state.documents)
        spec = CallSpec(
            call_id=f"research-{state.article.article_id}-r{state.rounds}",
            role=LLMRole.RESEARCH,
            model=self.model,
            prompt_version=PROMPT_VERSION,
        )
        return measured_call(
            spec,
            lambda _attempt: self.invoke(request),
            lambda value: accept(ExtractionOutput.model_validate(value), documents, prior=prior),
            pricing=self.pricing,
            policy=self.policy,
            record=self.record,
        )

    def _gather(
        self, state: _State, questions: tuple[QuestionDraft, ...], library: SourceLibrary, seen: _Seen, started: float
    ) -> tuple[tuple[str, ...], tuple[str, ...], bool]:
        """Act on the questions within the budget. Returns what was asked, the blocks, and whether a cap cut it short."""
        asked: list[str] = []
        blocks: list[str] = []
        capped = False
        share = max(1_000, self.budget.max_source_chars // max(1, len(questions)))
        for question in questions:
            if question.url is not None:
                url = self._allowed_url(state, question.url, seen)
                if url is None:
                    continue
                if len(state.references) >= self.budget.max_extra_pages or self._elapsed(started) >= self.budget.max_seconds * 1000:
                    state.unresolved.append(f"上限に達したため参照先を取得していない: {url}")
                    capped = True
                    continue
                document = self._read_reference(state, url, library, seen)
                if document is None:
                    continue
                block, omitted = render_reference(document, max_chars=share)
                if omitted:
                    state.unresolved.append(f"文字数の上限で {document.doc_id} の {len(omitted)} 段落を読んでいない")
            else:
                document = state.documents.get(question.doc or "main")
                wanted = tuple(p for p in question.paragraphs if _has(document, p)) if document else ()
                if not wanted:
                    continue
                state.paragraph_requests += len(wanted)
                block, _ = render_reference(document, max_chars=share, only=wanted)
            asked.append(question.question)
            blocks.append(block)
        return tuple(asked), tuple(blocks), capped

    def _allowed_url(self, state: _State, raw: str, seen: _Seen) -> str | None:
        try:
            url = canonicalize_url(raw)
        except UrlRejected:
            state.unresolved.append("取得先として不正な URL が提案された")
            return None
        if url not in state.links:
            # The one rule that keeps a page from steering research: only a link
            # the article's own body carries can be opened.
            state.unresolved.append(f"記事が参照していない URL は取得しない: {url}")
            return None
        if url in seen.urls or (self.processed is not None and self.processed.has_url(url)):
            state.unresolved.append(f"処理済みの URL は再取得しない: {url}")
            return None
        return url

    def _read_reference(self, state: _State, url: str, library: SourceLibrary, seen: _Seen) -> SourceDocument | None:
        seen.urls.add(url)
        outcome = self.fetch(url)
        if not isinstance(outcome, FetchedPage):
            state.references.append(FetchedReference(url=url, failure=f"取得に失敗した（{outcome.kind.value}）"))
            state.unresolved.append(f"参照先を取得できなかった: {url}")
            return None
        body = extract_document(outcome.html).body_text
        if len(body) < MIN_PRIMARY_INFO_CHARS:
            state.references.append(FetchedReference(url=url, failure="本文を抽出できなかった"))
            state.unresolved.append(f"参照先の本文を抽出できなかった: {url}")
            return None
        digest = compute_content_hash(body)
        if digest in seen.hashes or (self.processed is not None and self.processed.has_content_hash(digest)):
            state.references.append(FetchedReference(url=url, failure="既に読んだ本文と同じだった"))
            return None
        seen.hashes.add(digest)
        doc_id = f"ref{len([r for r in state.references if r.doc_id]) + 1}"
        document = source_document(state.article.article_id, doc_id, url, body)
        library.add(document)
        state.documents[doc_id] = document
        state.references.append(FetchedReference(url=url, doc_id=doc_id))
        return document

    def _elapsed(self, started: float) -> int:
        return max(0, round((self.monotonic() - started) * 1000))


@dataclass
class _Seen:
    urls: set[str]
    hashes: set[str]


@dataclass
class _State:
    article: NormalizedArticle
    cluster: Cluster
    links: tuple[str, ...]
    documents: dict[str, SourceDocument] = field(default_factory=dict)
    map: EvidenceMap = field(default_factory=EvidenceMap)
    rounds: int = 0
    references: list[FetchedReference] = field(default_factory=list)
    paragraph_requests: int = 0
    questions_considered: int = 0
    """Questions acted on or refused so far. A refused one counts, so proposing
    URLs the article does not cite cannot buy extra rounds of questions."""
    unresolved: list[str] = field(default_factory=list)

    def packet(self, assessment: Assessment, stop: ResearchStopReason, elapsed_ms: int) -> EvidencePacket:
        article = self.article
        return EvidencePacket(
            article_id=article.article_id,
            source_id=article.source_id,
            source_kind=article.source_kind,
            canonical_url=article.canonical_url,
            title=article.title,
            author=article.author,
            published_at=article.published_at,
            supporting_ids=tuple(member.article_id for member in self.cluster.supporting),
            concepts=self.map.concepts,
            claims=self.map.claims,
            evidence=self.map.evidence,
            limitations=self.map.limitations,
            unresolved=tuple(dict.fromkeys((*assessment.unmet, *self.unresolved))),
            status=assessment.status,
            stop_reason=stop,
            trace=ResearchTrace(
                rounds=self.rounds,
                extra_pages=tuple(self.references),
                paragraph_requests=self.paragraph_requests,
                elapsed_ms=elapsed_ms,
            ),
        )


def _failed(error: ErrorRecord | None) -> Assessment:
    kind = error.kind.value if error is not None else "unknown"
    return Assessment(ResearchStatus.INSUFFICIENT, (f"根拠を抽出できなかった（{kind}）",))


def _supporting_packet(article: NormalizedArticle, representative: NormalizedArticle) -> EvidencePacket:
    return EvidencePacket(
        article_id=article.article_id,
        source_id=article.source_id,
        source_kind=article.source_kind,
        canonical_url=article.canonical_url,
        title=article.title,
        author=article.author,
        published_at=article.published_at,
        represented_by=representative.article_id,
        unresolved=(f"同じトピックの {representative.article_id} を代わりに調べた",),
        status=ResearchStatus.INSUFFICIENT,
        stop_reason=ResearchStopReason.NOT_RESEARCHED,
        trace=ResearchTrace(rounds=0, elapsed_ms=0),
    )


def _has(document: SourceDocument | None, paragraph_id: str) -> bool:
    if document is None:
        return False
    try:
        document.paragraph(paragraph_id)
    except KeyError:
        return False
    return True
