"""Scoped BM25 retrieval, in pure Python and with no index dependency.

Two properties matter here and both are tested:

1. The scope filter runs BEFORE scoring, so a chunk the requester is not
   cleared for is never scored, never ranked, and never reaches the model.
2. The reported confidence is a bounded transform of the top BM25 score. It is
   a ranking heuristic used as an abstention trigger, not a probability.
"""

from __future__ import annotations

import math
import re
from collections import Counter

from app.config import Settings, get_settings
from app.models import Chunk, RetrievedChunk

K1 = 1.5
B = 0.75

STOPWORDS = frozenset(
    """
    a an and are as at be been by for from has have how in into is it its of on
    or that the their there these they this to was were what when where which
    who why will with within must may can does do
    """.split()
)

_TOKEN = re.compile(r"[a-z0-9][a-z0-9.,]*")

# A light suffix stemmer, so "approves", "approved", and "approval" match. It
# is crude and dependency free; a real analyzer drops in here without
# touching anything else.
#
# It strips one suffix, so it does not collapse every word family, and the
# example above is one it happens to get right:
#     approves/approved/approval      -> approv          (one stem, as claimed)
#     notify/notifies/notified/-ication -> notify/notifi/notific  (three)
#     validate/validated/validation   -> validat/valid   (two)
#     condition/conditions/conditional -> condit/condition (two)
# A question phrased around one member of a split family scores lower against
# a passage phrased around another. No golden case turns on it, because the
# co-occurring exact tokens carry those questions, so the weakness is latent.
#
# Applying the same rules until no suffix applies would be worse. It fixes
# `conditional` and splits `expense`/`expenses` into expen/expens, the exact
# collision the trailing-"e" rule below exists to prevent. A correct fix is a
# real stemmer, the drop-in mentioned above, not another rule bolted onto
# this one.
_SUFFIXES = (
    "ations",
    "ation",
    "ions",
    "ion",
    "ments",
    "ment",
    "als",
    "al",
    "ing",
    "ers",
    "er",
    "ed",
    "es",
    "s",
    "ly",
)


def stem(token: str) -> str:
    if any(character.isdigit() for character in token):
        return token
    if token.endswith("ies") and len(token) >= 5:
        token = token[:-3] + "y"
    else:
        for suffix in _SUFFIXES:
            if token.endswith(suffix) and len(token) - len(suffix) >= 4:
                token = token[: -len(suffix)]
                break
    # Strip a trailing "e" last, so the singular and plural of an "e" ending
    # word land on the same stem. Without this, "expenses" stems to "expens"
    # while "expense" stems to itself, and a question and the passage that
    # answers it can miss each other. A real model run surfaced exactly that.
    if token.endswith("e") and len(token) >= 5:
        token = token[:-1]
    return token


def tokenize(text: str) -> list[str]:
    """Lowercase, stemmed word tokens. Trailing punctuation stripped."""
    tokens = []
    for match in _TOKEN.findall(text.lower()):
        token = match.rstrip(".,")
        if token:
            tokens.append(stem(token))
    return tokens


def content_tokens(text: str) -> set[str]:
    return {token for token in tokenize(text) if token not in STOPWORDS}


class BM25Index:
    def __init__(self, chunks: list[Chunk]) -> None:
        self.chunks = chunks
        self.token_counts = [Counter(tokenize(chunk.text)) for chunk in chunks]
        self.lengths = [sum(counts.values()) for counts in self.token_counts]
        self.avg_length = (sum(self.lengths) / len(self.lengths)) if self.lengths else 0.0
        self.doc_frequency: Counter[str] = Counter()
        for counts in self.token_counts:
            self.doc_frequency.update(counts.keys())
        self.total_docs = len(chunks)

    def _idf(self, token: str) -> float:
        frequency = self.doc_frequency.get(token, 0)
        return math.log(1 + (self.total_docs - frequency + 0.5) / (frequency + 0.5))

    def score(self, query_tokens: list[str], index: int) -> float:
        counts = self.token_counts[index]
        length = self.lengths[index] or 1
        total = 0.0
        for token in query_tokens:
            frequency = counts.get(token, 0)
            if not frequency:
                continue
            denominator = frequency + K1 * (1 - B + B * length / (self.avg_length or 1))
            total += self._idf(token) * (frequency * (K1 + 1)) / denominator
        return total


def retrieve(
    question: str,
    chunks: list[Chunk],
    scopes: set[str],
    settings: Settings | None = None,
) -> tuple[list[RetrievedChunk], float]:
    """Return (top-k retrieved chunks, retrieval confidence in [0, 1])."""
    settings = settings or get_settings()

    # Scope filter first. Out-of-scope chunks are not scored at all.
    visible = [chunk for chunk in chunks if chunk.scope in scopes]
    if not visible:
        return [], 0.0

    index = BM25Index(visible)
    query_tokens = [token for token in tokenize(question) if token not in STOPWORDS]
    scored = [
        RetrievedChunk(chunk=chunk, score=index.score(query_tokens, position))
        for position, chunk in enumerate(visible)
    ]
    scored = [item for item in scored if item.score > 0.0]
    scored.sort(key=lambda item: (-item.score, item.chunk.chunk_id))
    top = scored[: settings.top_k]
    if not top:
        return [], 0.0
    confidence = min(1.0, top[0].score / settings.retrieval_saturation)
    return top, confidence
