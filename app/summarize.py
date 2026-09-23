"""Filing summarization.

Default: a dependency-free *extractive* summarizer — strip HTML, score
sentences by keyword hits and position, return the top sentences. No API
key needed.

Optional LLM hook: ``llm_summarize`` is a clearly-marked TODO stub. It
raises NotImplementedError with integration instructions instead of
inventing an API call.
"""

from __future__ import annotations

import os
import re
from html import unescape
from html.parser import HTMLParser

KEYWORDS = [
    "revenue", "guidance", "outlook", "risk", "acquisition", "merger",
    "restatement", "restated", "impairment", "buyback", "repurchase",
    "dividend", "earnings", "loss", "default", "lawsuit", "settlement",
    "ceo", "cfo", "resign", "appoint", "bankrupt", "going concern",
    "material weakness", "sec investigation", "contract", "fda approval",
]

_SKIP_TAGS = {"script", "style", "head", "title", "noscript"}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in _SKIP_TAGS or tag.startswith("ix:"):
            # Skip scripts/styles/head and inline-XBRL payloads (ix:header,
            # ix:hidden, ix:nonfraction, ...), whose text is tag noise.
            self._skip_depth += 1
        elif tag in {"p", "br", "div", "tr", "li", "h1", "h2", "h3", "h4"}:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if (tag in _SKIP_TAGS or tag.startswith("ix:")) and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self._parts.append(data)

    def text(self) -> str:
        return unescape(" ".join("".join(self._parts).split()))


def strip_html(html: str) -> str:
    """Return visible text from an HTML filing document."""
    parser = _TextExtractor()
    parser.feed(html)
    return parser.text()


_SENT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\(\"])")


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT_RE.split(text) if len(s.strip()) > 40]


def _score_sentence(sentence: str, position: int, total: int) -> float:
    low = sentence.lower()
    hits = sum(1 for kw in KEYWORDS if kw in low)
    # Early sentences (executive summary / MD&A lead-ins) get a bonus.
    position_bonus = max(0.0, 1.0 - position / max(total, 1)) * 0.5
    # Prefer substantive sentences, penalize boilerplate-length extremes.
    length_penalty = 0.0 if 60 < len(sentence) < 400 else -0.5
    return hits + position_bonus + length_penalty


def extractive_summary(text: str, max_sentences: int = 8, lead_sentences: int = 2) -> str:
    """Pick the most informative sentences: first ``lead_sentences`` plus
    top keyword-scoring sentences, kept in document order."""
    if "<" in text and ">" in text:
        text = strip_html(text)
    sentences = split_sentences(text)
    if not sentences:
        return ""
    scored = sorted(
        ((_score_sentence(s, i, len(sentences)), i, s) for i, s in enumerate(sentences)),
        reverse=True,
    )
    picks = {0} if sentences else set()
    picks.update(range(min(lead_sentences, len(sentences))))
    for _, i, _ in scored:
        if len(picks) >= max_sentences:
            break
        picks.add(i)
    return " ".join(sentences[i] for i in sorted(picks))


def summarize_filing_text(text: str, max_sentences: int = 8) -> str:
    """Summarize filing text. Uses the LLM hook when configured,
    otherwise the built-in extractive summarizer."""
    llm = llm_summarize(text)
    if llm:
        return llm
    return extractive_summary(text, max_sentences=max_sentences)


def llm_summarize(text: str) -> str | None:
    """TODO (optional LLM hook): summarize ``text`` with a language model.

    To enable:
      1. Set LLM_API_KEY in your .env.
      2. Implement this function with YOUR provider's official SDK or HTTP
         API (e.g. openai, anthropic, google-generativeai — install the
         package and follow its docs for chat/completions).
      3. Return the summary string, or None on failure to fall back to the
         extractive summarizer.

    Deliberately raises NotImplementedError instead of guessing at an API:
    wire it to the provider you actually use.
    """
    if not os.environ.get("LLM_API_KEY"):
        return None
    raise NotImplementedError(
        "LLM_API_KEY is set but llm_summarize() is not implemented. "
        "Edit app/summarize.py:llm_summarize and call your chosen provider's "
        "documented API, then return the summary text."
    )
