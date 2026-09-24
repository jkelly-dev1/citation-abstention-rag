"""The abstention policy: when the system refuses to answer, and why.

Reason codes are part of the contract. "I don't know" without a reason is not
auditable, and the reason is what a reviewer needs in order to tell a retrieval
gap apart from a model that made something up.
"""

from __future__ import annotations

from app.config import Settings, get_settings
from app.models import ClaimVerdict
from app.retrieval import content_tokens
from app.verify import answer_relevance

NO_RELEVANT_SOURCE = "no_relevant_source"
NO_CLAIMS = "model_produced_no_claims"
MODEL_DECLINED = "model_declined"
UNPARSEABLE = "model_output_unparseable"
NO_SUPPORTED_CLAIMS = "no_supported_claims"
INSUFFICIENT_SUPPORT = "insufficient_support"
ANSWER_NOT_RELEVANT = "answer_not_relevant"
PROVIDER_ERROR = "provider_unavailable"


class Decision:
    def __init__(
        self,
        status: str,
        reasons: list[str],
        served: list[ClaimVerdict],
        dropped: list[ClaimVerdict],
        partial: bool = False,
    ) -> None:
        self.status = status
        self.reasons = reasons
        self.served = served
        self.dropped = dropped
        self.partial = partial


def decide(
    retrieval_confidence: float,
    verdicts: list[ClaimVerdict],
    model_declined: bool = False,
    settings: Settings | None = None,
    question: str = "",
    parse_error: bool = False,
) -> Decision:
    settings = settings or get_settings()

    supported = [verdict for verdict in verdicts if verdict.supported]
    unsupported = [verdict for verdict in verdicts if not verdict.supported]

    # Nothing relevant was retrieved: abstain before the model output matters.
    if retrieval_confidence < settings.min_retrieval_confidence:
        return Decision("abstained", [NO_RELEVANT_SOURCE], [], unsupported + supported)

    # An unreadable reply is checked first, and reported as its own thing. The
    # system did not get an answer it could refuse; it got bytes it could not
    # read. A truncated reply from a length cap is the ordinary case, and an
    # operator reading the audit log has to be able to tell it apart from the
    # model genuinely declining, or a broken integration and a working
    # guardrail leave the same trace.
    if parse_error:
        return Decision("abstained", [UNPARSEABLE], [], verdicts)

    if model_declined:
        return Decision("abstained", [MODEL_DECLINED], [], verdicts)

    if not verdicts:
        return Decision("abstained", [NO_CLAIMS], [], [])

    if not supported:
        return Decision("abstained", [NO_SUPPORTED_CLAIMS], [], unsupported)

    # Some claims survived, but too few. A partly fabricated answer with the
    # fabrications quietly deleted still reads as authoritative, so refuse it.
    ratio = len(supported) / len(verdicts)
    if ratio < settings.min_supported_ratio:
        return Decision("abstained", [INSUFFICIENT_SUPPORT], [], unsupported + supported)

    # Grounded but off topic is still the wrong answer. Refuse it rather than
    # serve a well cited answer to a question nobody asked.
    if question:
        # The served claims, and nothing else. Including the verified quotes
        # here would score the wrong text: retrieval has already matched those
        # quotes on the question's own words, so they largely re-measure
        # retrieval and let an answer that says nothing about the question ride
        # in on the passage it cites. "Order records are kept", citing the real
        # retention sentence, would clear a question about how long trade
        # confirmations are retained. What the caller is served is the claim
        # text, so that is what has to address the question.
        relevance = answer_relevance(
            question, [verdict.text for verdict in supported]
        )
        if relevance < settings.min_answer_relevance:
            return Decision(
                "abstained", [ANSWER_NOT_RELEVANT], [], unsupported + supported
            )

        # Then each claim on its own. The score above is over the union of
        # the served claims, so one on-topic claim would carry any number of
        # grounded but off-question ones in with it. The shipped mock serves
        # two claims and the second is often padding.
        # A claim that shares no content word with the question is not an
        # answer to it. It is dropped, not refused: the claim is verified and
        # belongs in the audit record, it simply is not served, and `partial`
        # tells the caller something was held back.
        # The best-scoring claim is never dropped this way. If every claim
        # scored zero the union score above would already have abstained, so
        # reaching here means at least one claim addresses the question, and
        # keeping the best one means this rule cannot empty an answer that the
        # relevance threshold just accepted.
        #
        # What this does not catch. Sharing one content word is a low bar, and
        # some padding clears it: asked who approves above 5,000, the mock
        # serves the CFO sentence and the 500-to-5,000 director sentence,
        # which share five of the question's words and answer a different
        # question. Requiring each claim to add a question word no earlier
        # claim covered would remove that padding, and it would also withhold
        # a correct part of a legitimately multi-part answer: asked for the
        # expense approval thresholds, it would serve one band and withhold
        # the others, because they cover the same question words. Withholding
        # a true claim somebody asked for is the worse failure for a system
        # built not to withhold what it can support, so the weaker rule ships
        # and the gap is stated in the README limits. Telling "same words,
        # different band" from a real answer needs entailment, which is the
        # drop-in `claim_coverage` already points at.
        question_words = content_tokens(question)
        if question_words:
            ranked = sorted(
                supported,
                key=lambda verdict: -len(content_tokens(verdict.text) & question_words),
            )
            on_topic = [ranked[0]] + [
                verdict for verdict in ranked[1:]
                if content_tokens(verdict.text) & question_words
            ]
            off_topic = [v for v in supported if v not in on_topic]
            if off_topic:
                supported = [v for v in supported if v in on_topic]
                unsupported = unsupported + off_topic

    return Decision("answered", [], supported, unsupported, partial=bool(unsupported))
