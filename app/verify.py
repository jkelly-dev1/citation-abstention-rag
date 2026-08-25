"""Citation verification: the part that does not trust the model.

A citation survives only if all of these hold:

1. The cited chunk is one the retriever actually returned for this request.
   A chunk id the model never saw is a fabricated citation, and that includes
   any chunk the scope filter withheld.
2. The quote appears verbatim in that chunk after whitespace, case, and
   punctuation normalization, and it resolves to real character offsets in the
   source document.

A claim is then supported only if it has at least one surviving citation, its
content words are covered by those quotes, and every number in the claim also
appears in them. The overlap test is a lexical entailment proxy, not natural
language inference. It catches the common failure of a claim whose content is
simply not in the quote it cites; a stronger entailment check is a drop-in
replacement for `claim_coverage` and nothing else in the pipeline changes.
"""

from __future__ import annotations

import re

from app.config import Settings, get_settings
from app.models import Claim, ClaimVerdict, RetrievedChunk, VerifiedCitation
from app.retrieval import content_tokens

# Curly quotes and dashes are normalized so a model that prettifies a quote is
# not punished for it, while the words themselves still have to match.
_TRANSLATIONS = str.maketrans(
    {
        "‘": "'",
        "’": "'",
        "“": '"',
        "”": '"',
        "–": "-",
        "—": "-",
        " ": " ",
    }
)

_NUMBER = re.compile(r"\d[\d,.]*")


def normalize_with_map(text: str) -> tuple[str, list[int]]:
    """Normalize text and keep an index back to each original character.

    The map is what makes an offset-accurate citation possible: a match found
    in normalized space is translated back to the exact span in the source.
    """
    translated = text.translate(_TRANSLATIONS)
    out: list[str] = []
    index_map: list[int] = []
    previous_space = True  # leading whitespace is dropped
    for position, character in enumerate(translated):
        if character.isspace():
            if previous_space:
                continue
            out.append(" ")
            index_map.append(position)
            previous_space = True
            continue
        out.append(character.lower())
        index_map.append(position)
        previous_space = False
    while out and out[-1] == " ":
        out.pop()
        index_map.pop()
    return "".join(out), index_map


def normalize(text: str) -> str:
    return normalize_with_map(text)[0]


def locate_quote(quote: str, chunk_text: str) -> tuple[int, int] | None:
    """Return (start, end) offsets of `quote` within `chunk_text`, or None."""
    normalized_quote = normalize(quote)
    if not normalized_quote:
        return None
    normalized_chunk, index_map = normalize_with_map(chunk_text)
    position = normalized_chunk.find(normalized_quote)
    if position == -1:
        return None
    start = index_map[position]
    end = index_map[position + len(normalized_quote) - 1] + 1
    return start, end


def numbers_in(text: str) -> set[str]:
    return {match.rstrip(".,").replace(",", "") for match in _NUMBER.findall(text)}


def claim_coverage(claim_text: str, quotes: list[str]) -> float:
    """Fraction of the claim's content words that appear in its quotes."""
    claim_words = content_tokens(claim_text)
    if not claim_words:
        return 0.0
    quote_words: set[str] = set()
    for quote in quotes:
        quote_words |= content_tokens(quote)
    return len(claim_words & quote_words) / len(claim_words)


def answer_relevance(question: str, claim_texts: list[str]) -> float:
    """Fraction of the question's content words the answer actually addresses.

    Lexical retrieval can rank an on-vocabulary but off-topic passage highly,
    and a grounded answer to a different question is still the wrong answer.
    This is the check that catches it.
    """
    question_words = content_tokens(question)
    if not question_words:
        return 1.0
    answer_words: set[str] = set()
    for text in claim_texts:
        answer_words |= content_tokens(text)
    return len(question_words & answer_words) / len(question_words)


def verify_citation(
    chunk_id: str, quote: str, retrieved: dict[str, RetrievedChunk]
) -> VerifiedCitation:
    item = retrieved.get(chunk_id)
    if item is None:
        # The model cited something it was not shown. Refuse it as data.
        return VerifiedCitation(
            chunk_id=chunk_id, quote=quote, ok=False, reason="chunk_not_retrieved"
        )
    span = locate_quote(quote, item.chunk.text)
    if span is None:
        return VerifiedCitation(
            chunk_id=chunk_id,
            quote=quote,
            ok=False,
            reason="quote_not_found_in_source",
            doc_id=item.chunk.doc_id,
        )
    start, end = span
    return VerifiedCitation(
        chunk_id=chunk_id,
        quote=quote,
        ok=True,
        doc_id=item.chunk.doc_id,
        source_path=item.chunk.source_path,
        start=item.chunk.start + start,
        end=item.chunk.start + end,
    )


def verify_claim(
    claim: Claim,
    retrieved: dict[str, RetrievedChunk],
    settings: Settings | None = None,
) -> ClaimVerdict:
    settings = settings or get_settings()
    citations = [
        verify_citation(citation.chunk_id, citation.quote, retrieved)
        for citation in claim.citations
    ]
    verified_quotes = [citation.quote for citation in citations if citation.ok]

    reasons: list[str] = []
    if not claim.citations:
        reasons.append("no_citation")
    if claim.citations and not verified_quotes:
        reasons.append("no_verified_citation")

    coverage = claim_coverage(claim.text, verified_quotes) if verified_quotes else 0.0
    if verified_quotes and coverage < settings.min_claim_coverage:
        reasons.append("claim_not_covered_by_quote")

    if settings.require_numeric_grounding and verified_quotes:
        quoted_numbers: set[str] = set()
        for quote in verified_quotes:
            quoted_numbers |= numbers_in(quote)
        if numbers_in(claim.text) - quoted_numbers:
            reasons.append("ungrounded_number")

    return ClaimVerdict(
        text=claim.text,
        supported=not reasons,
        coverage=round(coverage, 4),
        citations=citations,
        reasons=reasons,
    )
