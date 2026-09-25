"""Bounded research: from a normalized article to the Evidence Packet the Selector reads.

Import from this package, not from its submodules.

- `Researcher.run` clusters the run's articles, researches each cluster's
  representative within `ResearchBudget`, and returns one packet per article
- `SourceLibrary` resolves an Evidence ID to its paragraph, in memory only
- `render_packet` is the Selector's ordinary input for one article: claims and
  short quotes, never the full text
"""

from .clusters import MIN_SHARED_TITLE_WORDS, OFFICIAL_SOURCE_PREFIXES, TITLE_SIMILARITY, Cluster, cluster_articles
from ..config import RESEARCH_KEY, load_research_budget
from .documents import render_article, render_reference, source_document, split_paragraphs
from .extraction import (
    MAX_CLAIMS,
    MAX_CONCEPTS,
    MAX_EVIDENCE,
    MAX_LIMITATIONS,
    MAX_QUESTIONS,
    ClaimDraft,
    ConceptDraft,
    EvidenceDraft,
    EvidenceMap,
    ExtractionOutput,
    LimitationDraft,
    QuestionDraft,
    Assessment,
    accept,
    assess,
)
from .library import SourceLibrary, render_map, render_packet
from .loop import DEFAULT_RETRY, Invoker, Researcher, ResearchInput, ResearchResult, resolve_links
from .prompt import PROMPT_VERSION, SYSTEM_PROMPT

__all__ = [
    "DEFAULT_RETRY",
    "MAX_CLAIMS",
    "MAX_CONCEPTS",
    "MAX_EVIDENCE",
    "MAX_LIMITATIONS",
    "MAX_QUESTIONS",
    "MIN_SHARED_TITLE_WORDS",
    "OFFICIAL_SOURCE_PREFIXES",
    "PROMPT_VERSION",
    "RESEARCH_KEY",
    "SYSTEM_PROMPT",
    "TITLE_SIMILARITY",
    "Assessment",
    "ClaimDraft",
    "Cluster",
    "ConceptDraft",
    "EvidenceDraft",
    "EvidenceMap",
    "ExtractionOutput",
    "Invoker",
    "LimitationDraft",
    "QuestionDraft",
    "ResearchInput",
    "ResearchResult",
    "Researcher",
    "SourceLibrary",
    "accept",
    "assess",
    "cluster_articles",
    "load_research_budget",
    "render_article",
    "render_map",
    "render_packet",
    "render_reference",
    "resolve_links",
    "source_document",
    "split_paragraphs",
]
