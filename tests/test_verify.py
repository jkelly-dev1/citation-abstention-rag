"""Verification: the tests that decide whether the liability argument holds."""

from __future__ import annotations

from pathlib import Path

from app.models import Citation, Claim, RetrievedChunk
from app.verify import (
    answer_relevance,
    claim_coverage,
    locate_quote,
    normalize,
    numbers_in,
    verify_citation,
    verify_claim,
)


def _retrieved(chunk_by_id, *chunk_ids) -> dict[str, RetrievedChunk]:
    return {
        chunk_id: RetrievedChunk(chunk=chunk_by_id[chunk_id], score=1.0)
        for chunk_id in chunk_ids
    }


def test_verified_citation_offsets_point_at_the_quote_in_the_source(chunk_by_id):
    retrieved = _retrieved(chunk_by_id, "expense-policy#s01")
    quote = "Expenses above 5,000 USD require written approval"
    citation = verify_citation("expense-policy#s01", quote, retrieved)
    assert citation.ok
    raw = Path(citation.source_path).read_text(encoding="utf-8")
    assert raw[citation.start : citation.end] == quote


def test_fabricated_quote_is_rejected(chunk_by_id):
    retrieved = _retrieved(chunk_by_id, "expense-policy#s01")
    citation = verify_citation(
        "expense-policy#s01",
        "The Chief Executive Officer has an annual travel budget of 250,000 USD.",
        retrieved,
    )
    assert not citation.ok
    assert citation.reason == "quote_not_found_in_source"


def test_citation_to_a_chunk_that_was_not_retrieved_is_rejected(chunk_by_id):
    """A model citing a chunk it was not shown is citing a source it invented.

    This is also what stops a model from reaching around the scope filter: the
    restricted chunk below is real, and its quote is real, and it is still
    refused because retrieval did not return it for this request.
    """
    retrieved = _retrieved(chunk_by_id, "expense-policy#s01")
    citation = verify_citation(
        "acme-fy2025-q4-summary#s01",
        "Acme Holdings reported revenue of 412.6 million USD",
        retrieved,
    )
    assert not citation.ok
    assert citation.reason == "chunk_not_retrieved"
    assert citation.start is None


def test_quote_matching_tolerates_whitespace_case_and_curly_quotes(chunk_by_id):
    chunk = chunk_by_id["data-retention-standard#s02"]
    quote = "a legal   hold OVERRIDES every retention period"
    span = locate_quote(quote, chunk.text)
    assert span is not None
    start, end = span
    assert normalize(chunk.text[start:end]) == normalize(quote)


def test_claim_quoting_a_real_sentence_but_stating_another_number_is_unsupported(
    chunk_by_id,
):
    """Mutation check: disable require_numeric_grounding and this test fails.

    The claim's words are almost entirely covered by a real quote, so lexical
    overlap alone accepts it. Only the numeric check catches the substitution,
    which is exactly the failure mode that matters for regulated data.
    """
    retrieved = _retrieved(chunk_by_id, "data-retention-standard#s01")
    claim = Claim(
        text="Trade confirmations and order records are retained for 10 years from the date of the transaction.",
        citations=[
            Citation(
                chunk_id="data-retention-standard#s01",
                quote="Trade confirmations and order records are retained for 7 years from the date of the transaction.",
            )
        ],
    )
    verdict = verify_claim(claim, retrieved)
    assert not verdict.supported
    assert "ungrounded_number" in verdict.reasons
    assert verdict.coverage > 0.8


def test_claim_whose_content_is_absent_from_its_quote_is_unsupported(chunk_by_id):
    retrieved = _retrieved(chunk_by_id, "expense-policy#s01")
    claim = Claim(
        text="Employees may expense first class flights to any destination worldwide.",
        citations=[
            Citation(
                chunk_id="expense-policy#s01",
                quote="Expenses above 5,000 USD require written approval",
            )
        ],
    )
    verdict = verify_claim(claim, retrieved)
    assert not verdict.supported
    assert "claim_not_covered_by_quote" in verdict.reasons


def test_claim_with_no_citation_is_unsupported(chunk_by_id):
    verdict = verify_claim(Claim(text="Anything at all.", citations=[]), {})
    assert not verdict.supported
    assert "no_citation" in verdict.reasons


def test_grounded_claim_is_supported(chunk_by_id):
    retrieved = _retrieved(chunk_by_id, "incident-response-runbook#s02")
    quote = "The incident commander notifies the Legal and Compliance leads within 1 hour"
    claim = Claim(
        text=quote,
        citations=[Citation(chunk_id="incident-response-runbook#s02", quote=quote)],
    )
    verdict = verify_claim(claim, retrieved)
    assert verdict.supported
    assert verdict.reasons == []
    assert verdict.citations[0].ok


def test_numbers_and_coverage_helpers():
    assert numbers_in("revenue of 412.6 million USD in 2025") == {"412.6", "2025"}
    assert numbers_in("5,000 USD") == {"5000"}
    assert claim_coverage("legal hold stops deletion", ["a legal hold stops deletion"]) == 1.0
    assert claim_coverage("entirely unrelated wording", ["legal hold"]) == 0.0


def test_answer_relevance_scores_off_topic_answers_low():
    question = "What revenue did Acme Holdings report for the fourth quarter?"
    on_topic = ["Acme Holdings reported revenue of 412.6 million USD for the fourth quarter."]
    off_topic = ["An independent validator must sign the validation report."]
    assert answer_relevance(question, on_topic) > 0.8
    assert answer_relevance(question, off_topic) < 0.4
