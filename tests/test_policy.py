"""The abstention policy, one reason code at a time."""

from __future__ import annotations

from app.models import ClaimVerdict, VerifiedCitation
from app.policy import (
    ANSWER_NOT_RELEVANT,
    INSUFFICIENT_SUPPORT,
    MODEL_DECLINED,
    NO_CLAIMS,
    NO_RELEVANT_SOURCE,
    NO_SUPPORTED_CLAIMS,
    UNPARSEABLE,
    decide,
)


def _supported(text: str, quote: str | None = None) -> ClaimVerdict:
    return ClaimVerdict(
        text=text,
        supported=True,
        coverage=1.0,
        citations=[
            VerifiedCitation(chunk_id="c#s01", quote=quote or text, ok=True, start=0, end=1)
        ],
    )


def _unsupported(text: str) -> ClaimVerdict:
    return ClaimVerdict(
        text=text, supported=False, coverage=0.0, reasons=["quote_not_found_in_source"]
    )


def test_weak_retrieval_abstains_before_the_model_output_matters(settings):
    decision = decide(0.1, [_supported("anything")], settings=settings)
    assert decision.status == "abstained"
    assert decision.reasons == [NO_RELEVANT_SOURCE]
    assert decision.served == []


def test_model_declining_is_honoured(settings):
    decision = decide(0.9, [], model_declined=True, settings=settings)
    assert decision.reasons == [MODEL_DECLINED]


def test_an_unreadable_reply_gets_its_own_reason_code(settings):
    """`model_output_unparseable` is not `model_declined`.

    Both abstain, so a test that only asserted abstention would pass with the
    two collapsed into one code. The reason code is what an operator triages
    the audit log by.

    Mutation check: delete the `parse_error` branch in `decide` and this goes
    red with `model_produced_no_claims`; route `parse_error` to MODEL_DECLINED
    and it goes red on the inequality below.
    """
    decision = decide(0.9, [], settings=settings, parse_error=True)
    assert decision.status == "abstained"
    assert decision.reasons == [UNPARSEABLE]
    assert decision.reasons != [MODEL_DECLINED]


def test_an_unreadable_reply_outranks_every_later_branch(settings):
    """Order matters: the earlier branches must not swallow it.

    `parse_error` arrives with no claims, so `model_produced_no_claims` would
    fire for it if the branch were placed after that one, silently, with a
    reason code that says the model produced nothing when in fact the system
    could not read what it produced.
    """
    decision = decide(
        0.9, [], model_declined=True, settings=settings, parse_error=True
    )
    assert decision.reasons == [UNPARSEABLE]


def test_no_claims_abstains(settings):
    assert decide(0.9, [], settings=settings).reasons == [NO_CLAIMS]


def test_all_claims_unsupported_abstains(settings):
    decision = decide(0.9, [_unsupported("made up")], settings=settings)
    assert decision.status == "abstained"
    assert decision.reasons == [NO_SUPPORTED_CLAIMS]
    assert decision.served == []
    assert len(decision.dropped) == 1


def test_too_few_supported_claims_abstains_rather_than_serving_a_fragment(settings):
    """Mutation check: set min_supported_ratio to 0 and this test fails.

    Silently deleting the fabricated half of an answer and serving the rest
    still presents a confident answer built on a model that was making things
    up. The system refuses the whole answer instead.
    """
    verdicts = [_supported("real claim about expenses"), _unsupported("invented claim")]
    decision = decide(0.9, verdicts, settings=settings, question="expenses claim real")
    assert decision.status == "abstained"
    assert decision.reasons == [INSUFFICIENT_SUPPORT]
    assert decision.served == []


def test_majority_supported_answers_and_flags_partial(settings):
    verdicts = [
        _supported("expense approval threshold claim one"),
        _supported("expense approval threshold claim two"),
        _unsupported("invented claim"),
    ]
    decision = decide(
        0.9, verdicts, settings=settings, question="expense approval threshold claim"
    )
    assert decision.status == "answered"
    assert len(decision.served) == 2
    assert decision.partial is True
    assert all(verdict.supported for verdict in decision.served)


def test_grounded_but_off_topic_answer_is_refused(settings):
    """Mutation check: set min_answer_relevance to 0 and this test fails."""
    verdicts = [_supported("An independent validator must sign the validation report.")]
    decision = decide(
        0.9,
        verdicts,
        settings=settings,
        question="What revenue did Acme Holdings report for the fourth quarter?",
    )
    assert decision.status == "abstained"
    assert decision.reasons == [ANSWER_NOT_RELEVANT]


def test_relevant_supported_answer_is_served(settings):
    verdicts = [_supported("Trade confirmations are retained for 7 years.")]
    decision = decide(
        0.9, verdicts, settings=settings, question="How long are trade confirmations retained?"
    )
    assert decision.status == "answered"
    assert decision.partial is False


def test_relevance_is_scored_over_the_answer_not_over_its_quotes(settings):
    """A claim that says nothing about the question is refused.

    Scoring the served claims TOGETHER WITH their verified quotes measures the
    wrong text: the quotes come from the chunk BM25 already matched on the
    question's own words, so the check largely re-measures retrieval and an
    answer can ride in on the passage it cites. This claim shares no content
    word with the question; its quote shares several.

    Mutation check: append `[citation.quote for verdict in supported for
    citation in verdict.citations if citation.ok]` back into the
    `answer_relevance` call in `decide` and this goes red, because the answer
    is served.
    """
    question = "How long are trade confirmations retained?"
    quote = (
        "Trade confirmations and order records are retained for 7 years from "
        "the date of the transaction."
    )
    verdict = _supported("Order records are kept.", quote=quote)

    # The quote on its own WOULD clear the threshold, which is what made this
    # answerable before. Assert that, so the test cannot pass for the trivial
    # reason that nothing here is relevant to anything.
    from app.verify import answer_relevance

    assert answer_relevance(question, [quote]) >= settings.min_answer_relevance
    assert answer_relevance(question, [verdict.text]) < settings.min_answer_relevance

    decision = decide(0.9, [verdict], settings=settings, question=question)
    assert decision.status == "abstained"
    assert decision.reasons == [ANSWER_NOT_RELEVANT]
    assert decision.served == []
