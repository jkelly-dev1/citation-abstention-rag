"""Scoped retrieval. The scope filter is an access control boundary, not a hint."""

from __future__ import annotations

from app.retrieval import BM25Index, retrieve, stem, tokenize


def test_out_of_scope_chunks_are_never_returned(chunks, settings):
    question = "What revenue did Acme Holdings report for the fourth quarter?"
    retrieved, _ = retrieve(question, chunks, {"public", "internal"}, settings)
    assert all(item.chunk.scope != "restricted" for item in retrieved)
    assert all("acme" not in item.chunk.doc_id for item in retrieved)


def test_same_question_with_clearance_returns_the_restricted_document(chunks, settings):
    question = "What revenue did Acme Holdings report for the fourth quarter?"
    retrieved, confidence = retrieve(
        question, chunks, {"public", "internal", "restricted"}, settings
    )
    assert retrieved[0].chunk.doc_id == "acme-fy2025-q4-summary"
    assert confidence > settings.min_retrieval_confidence


def test_scope_filter_runs_before_scoring(chunks, settings):
    """Mutation check: the filter must precede scoring, not post-filter results.

    A restricted chunk that outranks everything else would still be ranked
    first and then sliced away by top_k if the filter ran last, silently
    dropping the answer the requester was entitled to see.
    """
    # This question ranks the restricted summary above everything else, and
    # also matches internal chunks. With the filter last, the restricted chunk
    # would take a top_k slot and then be discarded, costing the requester an
    # internal chunk they were entitled to.
    question = "Acme Holdings revenue segment and the retention of trade records"
    unfiltered, _ = retrieve(question, chunks, {"internal", "restricted"}, settings)
    assert unfiltered[0].chunk.scope == "restricted"

    retrieved, _ = retrieve(question, chunks, {"internal"}, settings)
    assert len(retrieved) == settings.top_k
    assert all(item.chunk.scope == "internal" for item in retrieved)


def test_no_matching_terms_yields_zero_confidence(chunks, settings):
    retrieved, confidence = retrieve(
        "What is the capital of Iceland?", chunks, {"internal"}, settings
    )
    assert retrieved == []
    assert confidence == 0.0


def test_a_stemmer_collision_still_lands_below_the_abstention_threshold(chunks, settings):
    """A crude stemmer collides, and the threshold is what absorbs it.

    "office" and "officer" stem alike, so an unanswerable question about a
    Zurich office weakly matches the passage naming the Chief Financial
    Officer. The match is real but far below threshold, so the system still
    abstains. This is the intended failure direction.
    """
    retrieved, confidence = retrieve(
        "How many people work in the Zurich office?", chunks, {"internal"}, settings
    )
    assert retrieved  # a weak match exists
    assert confidence < settings.min_retrieval_confidence


def test_confidence_is_bounded_at_one(chunks, settings):
    _, confidence = retrieve(
        "expense expense expense approval approval thresholds",
        chunks,
        {"internal"},
        settings,
    )
    assert 0.0 <= confidence <= 1.0


def test_unknown_scope_label_retrieves_nothing(chunks, settings):
    retrieved, confidence = retrieve(
        "Who approves an expense above 5,000 USD?", chunks, {"nonexistent"}, settings
    )
    assert retrieved == []
    assert confidence == 0.0


def test_stemmer_unifies_common_inflections():
    assert stem("approves") == stem("approved") == stem("approval")
    assert stem("submitting") == stem("submitted")
    # Regression: a real Anthropic run abstained on an answerable question
    # because "expense" and "expenses" stemmed differently, so the question and
    # the passage answering it never matched.
    assert stem("expense") == stem("expenses")
    assert stem("policy") == stem("policies")
    assert stem("date") == stem("dates")
    # Numbers must survive untouched or numeric grounding breaks.
    assert stem("5,000") == "5,000"
    assert stem("412.6") == "412.6"


def test_ranking_prefers_the_chunk_that_answers_the_question(chunks, settings):
    retrieved, _ = retrieve(
        "How long are trade confirmations retained?", chunks, {"internal"}, settings
    )
    assert retrieved[0].chunk.chunk_id == "data-retention-standard#s01"


def test_bm25_scores_are_zero_for_absent_terms(chunks):
    index = BM25Index(chunks)
    assert index.score(tokenize("zurich helsinki"), 0) == 0.0


def test_the_stemmer_does_not_collapse_every_word_family():
    """A known-limitation pin, written to fail when a real analyzer lands.

    The stemmer strips one suffix, so it collapses the family its own comment
    advertises and splits others. This pins the limitation as it is, so that
    swapping in a real analyzer is a visible, deliberate change, and the
    README bullet describing it cannot stop being true in either direction
    unnoticed.

    Applying the same rules until no suffix applies would be worse: it fixes
    `conditional` and splits `expense`/`expenses`, the collision the
    trailing-"e" rule exists to prevent.
    """
    # The family the comment advertises really does collapse.
    assert len({stem(w) for w in ("approves", "approved", "approval")}) == 1
    # The families it splits stay split. If a real analyzer lands, these go
    # red and the README bullet comes out with them.
    assert len({stem(w) for w in ("notify", "notifies", "notification")}) > 1
    assert len({stem(w) for w in ("validate", "validation")}) > 1
    # The collision the trailing-"e" rule prevents must stay prevented.
    assert stem("expense") == stem("expenses")
