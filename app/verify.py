"""Citation verification: the part that does not trust the model.

A citation survives only if all of these hold:

1. The cited chunk is one the retriever actually returned for this request.
   A chunk id the model never saw is a fabricated citation, and that includes
   any chunk the scope filter withheld.
2. The quote appears verbatim in that chunk after whitespace, case, and
   punctuation normalization, and it resolves to real character offsets in the
   source document.

A claim is then supported only if it has at least one surviving citation, its
content words are covered by those quotes, every number in the claim also
appears in them, and its polarity words match theirs.

The overlap test measures vocabulary, not entailment. It catches the common
failure of a claim whose content is simply not in the quote it cites, and it
is blind by construction to a claim that reuses the quote's words and changes
what they say. Two such rewrites are caught by separate checks rather than by
the fraction, because each turns on a single token that the fraction cannot
weigh: a changed figure (`ungrounded_number`) and a flipped polarity
(`negation_mismatch`). Anything subtler than those two still passes. A real
entailment model is a drop-in replacement for `claim_coverage` and nothing
else in the pipeline changes.
"""

from __future__ import annotations

import re

from app.config import Settings, get_settings
from app.models import Claim, ClaimVerdict, RetrievedChunk, VerifiedCitation
from app.retrieval import content_tokens, stem

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

#: The word attached to a number is part of the number. "412.6 million" and
#: "412.6 billion" share the digits and differ by a factor of a thousand;
#: "7 years" and "7 days" share the digits and differ by a factor of 365; USD
#: and EUR share them and are different currencies. Grounding the digit string
#: alone would accept every one of those against the sentence that says
#: otherwise, with a resolvable citation attached, and on financial text that
#: is the failure that matters most.
#:
#: Spelled-out numbers are here for the same reason. "ten years" against a
#: quote saying "7 years" contains no digit at all, so `numbers_in` finds an
#: empty set and a digits-only check would pass by vacuum.
_MAGNITUDES = frozenset(
    {"hundred", "thousand", "million", "billion", "trillion", "k", "m", "bn"}
)
_UNITS = frozenset(
    {
        "second", "seconds", "minute", "minutes", "hour", "hours",
        "day", "days", "week", "weeks", "month", "months",
        "year", "years", "quarter", "quarters",
        "percent", "percentage", "pct",
        "usd", "eur", "gbp", "jpy", "chf", "cad", "aud",
        "dollar", "dollars", "euro", "euros", "pound", "pounds",
    }
)
_WORD_NUMBERS = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
    "ten": "10", "eleven": "11", "twelve": "12", "thirteen": "13",
    "fourteen": "14", "fifteen": "15", "sixteen": "16", "seventeen": "17",
    "eighteen": "18", "nineteen": "19", "twenty": "20", "thirty": "30",
    "forty": "40", "fifty": "50", "sixty": "60", "seventy": "70",
    "eighty": "80", "ninety": "90",
}
_QUANTITY = re.compile(
    r"(?<![\w.])(\d[\d,.]*|" + "|".join(_WORD_NUMBERS) + r")"
    r"(?:\s+([A-Za-z%]+))?",
    re.IGNORECASE,
)

#: Words that flip a sentence's polarity. Coverage is a bag of words, so a
#: claim that inverts its quote keeps almost all of that quote's vocabulary.
#: Negate the third sentence of expense-policy#s01 ("Expenses above 5,000 USD
#: require written approval from the Chief Financial Officer before the
#: expense is incurred") and the result scores 0.92 against the sentence it
#: contradicts. The quote has 12 content words, the negated claim has 13, and
#: the added "not" is the only one of the 13 the quote does not contain.
#: `scripts/check_readme_numbers.py` derives that figure by calling
#: `claim_coverage` on the full sentence, so the README cannot drift from it.
#: Weighting cannot fix this, because one token decides the meaning, so
#: polarity is compared as a set, separately from the overlap fraction.
#: The words are stemmed at import so the comparison happens in the same token
#: space `content_tokens` produces. Four of them change under `stem`
#: ("unless" -> "unles", "neither" -> "neith", "excluding" -> "exclud",
#: "prohibited" -> "prohibit"), and a raw-word set would never match them.
#: "never" and "except" are short enough to be left alone.
NEGATIONS = frozenset(
    stem(word)
    for word in (
        "not",
        "no",
        "never",
        "none",
        "cannot",
        "nor",
        "neither",
        "without",
        "unless",
        "except",
        "excluding",
        "prohibited",
    )
)


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


def quantities_in(text: str) -> set[str]:
    """Every number in `text`, with the magnitude, unit or currency on it.

    A quantity is normalized to "<digits>" or "<digits> <qualifier>", so
    "412.6 million" and "412.6 billion" are different members of the set while
    "7" and "7 years" are too. Spelled-out numbers are mapped to digits first,
    so "ten years" and "10 years" are the same quantity and a claim written
    either way is checked against a quote written the other.

    Only a magnitude, a unit or a currency is taken as the qualifier, never
    the next word whatever it is. "5,000 USD require written approval" must
    not yield "5000 require", because then a faithful paraphrase that says
    "5,000 USD needs written approval" would carry an ungrounded quantity and
    be refused, which is over-abstention caused by the grounding check itself.
    The bare "<digits>" form is emitted alongside the qualified one for the same
    reason: a claim that says "5,000" where the quote says "5,000 USD" is
    still grounded in the number.
    """
    found: set[str] = set()
    for match in _QUANTITY.finditer(text):
        raw, qualifier = match.group(1), match.group(2)
        digits = _WORD_NUMBERS.get(raw.lower(), raw).rstrip(".,").replace(",", "")
        found.add(digits)
        if qualifier:
            word = qualifier.lower().rstrip(".,")
            if word in _MAGNITUDES or word in _UNITS or word == "%":
                found.add(f"{digits} {word}")
    return found


def claim_coverage(claim_text: str, quotes: list[str]) -> float:
    """Fraction of the claim's content words that appear in its quotes."""
    claim_words = content_tokens(claim_text)
    if not claim_words:
        return 0.0
    quote_words: set[str] = set()
    for quote in quotes:
        quote_words |= content_tokens(quote)
    return len(claim_words & quote_words) / len(claim_words)


def negation_mismatch(claim_text: str, quotes: list[str]) -> bool:
    """True when the claim's polarity words are not the quotes' polarity words.

    The test is symmetric. A claim that adds a negation its quote does not have
    contradicts it, and a claim that drops one the quote does have contradicts
    it just as badly. Either way the two sentences do not say the same thing,
    and the overlap fraction cannot see the difference.

    This over-abstains: a claim that legitimately paraphrases "no employee
    may X unless Y" into "employees may X only after Y" is refused. The README
    limits section lists this as a deliberate failure direction.
    """
    claim_negations = content_tokens(claim_text) & NEGATIONS
    quote_negations: set[str] = set()
    for quote in quotes:
        quote_negations |= content_tokens(quote) & NEGATIONS
    return claim_negations != quote_negations


_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def quote_sentences(quotes: list[str]) -> list[str]:
    """Every quote split into sentences, without duplicates.

    A quote that does not split comes back whole, which is how a fragment (a
    list item, a heading, a clause ending in an abbreviation) keeps working.
    A quote that does split contributes its sentences and not itself. That
    exclusion is the whole mechanism: the union of a quote's sentences is the
    quote, so offering both would make the split decorative and let a claim
    assembled across two sentences pass.
    """
    out: list[str] = []
    for quote in quotes:
        pieces = [piece.strip() for piece in _SENTENCE_END.split(quote.strip())]
        pieces = [piece for piece in pieces if piece]
        for piece in pieces:
            if piece not in out:
                out.append(piece)
    return out


def best_supporting_quote(
    claim_text: str, quotes: list[str], settings: Settings
) -> tuple[str | None, float]:
    """-> (the one sentence that best supports the claim, its coverage).

    Scoring over the union of every verified quote would let a long quote
    support anything inside it. On the shipped corpus, quoting both sentences
    of data-retention-standard#s01 and claiming "Trade confirmations and order
    records are retained for 3 years" (the subject from one sentence, the
    figure from the other, and wrong) scores 1.00 against the union. Numeric
    grounding would not catch it either, for the same reason: the union
    contains "3 years", just not in the sentence that covers the words.

    A claim must therefore be carried by one sentence, with the words and the
    quantities coming from the same place. The sentence with the best coverage
    that also grounds every quantity wins. If none grounds the quantities, the
    best coverage is still returned so the caller can report the real reason.

    This deliberately over-abstains on a claim that faithfully synthesizes two
    adjacent sentences. The README limits section lists that direction, and
    no weaker rule closes the hole above.
    """
    candidates = quote_sentences(quotes)
    if not candidates:
        return None, 0.0
    scored = sorted(
        ((claim_coverage(claim_text, [piece]), piece) for piece in candidates),
        key=lambda pair: (-pair[0], len(pair[1])),
    )
    wanted = quantities_in(claim_text)
    for coverage, piece in scored:
        if not (wanted - quantities_in(piece)):
            return piece, coverage
    return scored[0][1], scored[0][0]


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

    # One sentence has to carry the claim. See `best_supporting_quote`.
    support, coverage = (
        best_supporting_quote(claim.text, verified_quotes, settings)
        if verified_quotes
        else (None, 0.0)
    )
    if verified_quotes and coverage < settings.min_claim_coverage:
        reasons.append("claim_not_covered_by_quote")

    if verified_quotes and negation_mismatch(claim.text, verified_quotes):
        reasons.append("negation_mismatch")

    if settings.require_numeric_grounding and support is not None:
        if quantities_in(claim.text) - quantities_in(support):
            reasons.append("ungrounded_number")

    return ClaimVerdict(
        text=claim.text,
        supported=not reasons,
        coverage=round(coverage, 4),
        citations=citations,
        reasons=reasons,
    )
