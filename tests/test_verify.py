"""Verification: the tests that decide whether the liability argument holds."""

from __future__ import annotations

from pathlib import Path

from app.models import Citation, Claim, RetrievedChunk
import re

from app.verify import (
    answer_relevance,
    claim_coverage,
    locate_quote,
    negation_mismatch,
    normalize,
    numbers_in,
    quantities_in,
    quote_sentences,
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


def test_a_claim_that_negates_its_own_quote_is_unsupported(chunk_by_id):
    """A flat contradiction is not a subtle misreading.

    Coverage is a bag of words, so inverting a sentence keeps almost all of
    its vocabulary. The claim below is an abbreviation of the source sentence
    and scores 0.875 against the quote it cites, because 7 of its 8 content
    words appear there. The 0.92 quoted in README.md and in `app/verify.py`
    belongs to the full sentence negated, which has 13 content words against
    the quote's 12. At either length, without a polarity check this is served
    as verified, carrying a resolvable citation to the sentence that says the
    opposite.

    Mutation check: remove the `negation_mismatch` branch from `verify_claim`
    and this goes red, because the claim is supported.
    """
    retrieved = _retrieved(chunk_by_id, "expense-policy#s01")
    quote = "Expenses above 5,000 USD require written approval"
    claim = Claim(
        text="Expenses above 5,000 USD do not require written approval.",
        citations=[Citation(chunk_id="expense-policy#s01", quote=quote)],
    )
    verdict = verify_claim(claim, retrieved)

    # The citation itself is fine: the quote is real and resolves. What fails
    # is the claim built on it.
    assert verdict.citations[0].ok
    assert verdict.coverage > 0.6
    assert not verdict.supported
    assert "negation_mismatch" in verdict.reasons


def test_a_faithful_claim_is_not_punished_by_the_negation_check(chunk_by_id):
    """The other half: the guard must not refuse an honest quote.

    A check that refused everything would satisfy the test above. This one
    fails if the polarity comparison is made unconditional.
    """
    retrieved = _retrieved(chunk_by_id, "expense-policy#s01")
    quote = "Expenses above 5,000 USD require written approval"
    claim = Claim(
        text=quote + ".",
        citations=[Citation(chunk_id="expense-policy#s01", quote=quote)],
    )
    verdict = verify_claim(claim, retrieved)
    assert verdict.supported
    assert verdict.reasons == []


def test_negation_mismatch_is_symmetric():
    """Dropping a negation contradicts the quote as surely as adding one."""
    assert negation_mismatch("The report is not required.", ["The report is required."])
    assert negation_mismatch("The report is required.", ["The report is not required."])
    assert not negation_mismatch("The report is required.", ["The report is required."])


def test_an_empty_or_whitespace_quote_is_refused(chunk_by_id):
    """An empty quote must not resolve to offset 0 of the source.

    `"".find` returns 0, so without the guard in `locate_quote` an empty quote
    would verify against the start of any retrieved chunk, giving a citation
    that points at real text nobody quoted.

    Mutation check: delete `if not normalized_quote: return None` from
    `locate_quote` and this goes red on the first case.
    """
    retrieved = _retrieved(chunk_by_id, "expense-policy#s01")
    for quote in ("", "   ", "\n\t "):
        citation = verify_citation("expense-policy#s01", quote, retrieved)
        assert not citation.ok, repr(quote)
        assert citation.reason == "quote_not_found_in_source"
        # It must not carry a span either. An offset pair is what a caller
        # highlights, and an assertion on `ok is False` alone would let one
        # through.
        assert citation.start is None and citation.end is None
    assert locate_quote("", chunk_by_id["expense-policy#s01"].text) is None


def test_a_changed_magnitude_unit_or_currency_is_not_grounded(chunk_by_id):
    """The word attached to a number is part of the number.

    "412.6 million" and "412.6 billion" share every digit and differ by a
    factor of a thousand. Grounding the digit string alone would serve all
    three of these against the sentence that says otherwise, each carrying a
    resolvable citation, which is the failure mode README calls the one that
    matters most on financial text.

    Mutation check: change `quantities_in` to `numbers_in` in `verify_claim`
    and this goes red, because all three are supported.
    """
    retrieved = _retrieved(chunk_by_id, "expense-policy#s01")
    quote = "Expenses above 5,000 USD require written approval"

    swapped_currency = verify_claim(
        Claim(text="Expenses above 5,000 EUR require written approval.",
              citations=[Citation(chunk_id="expense-policy#s01", quote=quote)]),
        retrieved,
    )
    assert not swapped_currency.supported
    assert "ungrounded_number" in swapped_currency.reasons
    # The citation itself is fine: the quote is real and resolves. What fails
    # is the quantity built on it.
    assert swapped_currency.citations[0].ok


def test_a_faithful_paraphrase_is_not_punished_by_quantity_grounding(chunk_by_id):
    """Quantity grounding does not refuse a faithful paraphrase.

    Only a magnitude, a unit or a currency counts as the qualifier. Taking the
    next word whatever it was would make "5,000 USD require" a quantity, and
    then the faithful paraphrase "5,000 USD needs written approval" would be
    refused for an ungrounded number that is really a synonym.

    Mutation check: drop the `word in _MAGNITUDES or word in _UNITS` test in
    `quantities_in` and this goes red.
    """
    retrieved = _retrieved(chunk_by_id, "expense-policy#s01")
    quote = "Expenses above 5,000 USD require written approval"
    verdict = verify_claim(
        Claim(text="Expenses above 5,000 USD need written approval.",
              citations=[Citation(chunk_id="expense-policy#s01", quote=quote)]),
        retrieved,
    )
    assert verdict.supported
    assert "ungrounded_number" not in verdict.reasons

    # The rule itself, directly. The assertion above passes whether or not
    # the qualifier is restricted, because the word after the number is "USD"
    # in both the claim and the quote. A non-unit word must not become part of
    # the quantity.
    assert quantities_in("5,000 require written approval") == {"5000"}


def test_a_spelled_out_number_is_the_same_quantity_as_its_digits():
    """"seven years" and "7 years" are one quantity, not two.

    Without the mapping, a claim written in words contains no digit at all,
    so the grounding check would compare an empty set and pass by vacuum. The
    check would be most permissive exactly where the claim was least like its
    quote.

    Mutation check: empty `_WORD_NUMBERS` and this goes red.
    """
    assert quantities_in("retained for seven years") == quantities_in(
        "retained for 7 years"
    )
    # A genuinely different value stays different, or the mapping would hide
    # the error instead of normalizing the spelling.
    assert quantities_in("retained for ten years") != quantities_in(
        "retained for 7 years"
    )
    # Directly: the word must resolve to its digits. The inequality above
    # holds even with the mapping gone, because two unmapped words differ from
    # each other too.
    assert "7 years" in quantities_in("retained for seven years")


def test_a_bare_number_is_still_grounded_by_a_qualified_quote():
    """A claim saying "5,000" against a quote saying "5,000 USD" is grounded.

    `quantities_in` emits the bare form alongside the qualified one for this
    reason. Emitting only the qualified form would refuse a claim that simply
    said less than its source, which is not a fabrication.
    """
    # Assert the bare form is present. `not (A - B)` is also satisfied when A
    # is empty, so the difference alone would pass with the bare form removed
    # entirely.
    assert "5000" in quantities_in("above 5,000 USD")
    assert not (quantities_in("above 5,000") - quantities_in("above 5,000 USD"))


def test_a_claim_must_be_carried_by_one_sentence_of_its_quote(chunk_by_id):
    """Coverage over the union would let a long quote support anything in it.

    Quoting both sentences of the retention chunk and claiming "Trade
    confirmations and order records are retained for 3 years" (the subject
    from one sentence, the figure from the other, and wrong) scores 1.00
    against the union. Numeric grounding would not catch it either: the union
    contains "3 years", just not in the sentence that covers the words.

    Mutation check: score coverage over `verified_quotes` instead of the best
    sentence and this goes red.
    """
    chunk = chunk_by_id["data-retention-standard#s01"]
    both = " ".join(
        re.split(r"(?<=\.)\s+", chunk.text.strip())[:2]
    )
    retrieved = _retrieved(chunk_by_id, "data-retention-standard#s01")

    def verdict(text):
        return verify_claim(
            Claim(text=text, citations=[Citation(
                chunk_id="data-retention-standard#s01", quote=both)]),
            retrieved,
        )

    crossed = verdict("Trade confirmations and order records are retained for 3 years.")
    assert not crossed.supported
    # Assert the citation itself is fine: what fails is the claim built across
    # two sentences, not the quote.
    assert crossed.citations[0].ok

    # Both faithful claims must still be served, or the rule is just a way of
    # refusing multi-sentence quotes.
    assert verdict(
        "Trade confirmations and order records are retained for 7 years."
    ).supported
    assert verdict("General business correspondence is retained for 3 years.").supported


def test_a_single_sentence_quote_is_unaffected_by_the_sentence_split():
    """A fragment with no sentence end must still be usable whole.

    `quote_sentences` returns a quote that does not split as itself. It must
    not return a quote that does split alongside its parts, because the union
    of the sentences is exactly the whole and the check would be defeated.
    """
    assert quote_sentences(["Expenses above 5,000 USD require approval"]) == [
        "Expenses above 5,000 USD require approval"
    ]
    # A quote that does split contributes its sentences and not itself.
    pieces = quote_sentences(["One thing is true. Another thing is false."])
    assert pieces == ["One thing is true.", "Another thing is false."]
    assert "One thing is true. Another thing is false." not in pieces
