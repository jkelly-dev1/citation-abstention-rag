"""The abstention policy: when the system refuses to answer, and why.

Reason codes are part of the contract. "I don't know" without a reason is not
auditable, and the reason is what a reviewer needs in order to tell a retrieval
gap apart from a model that made something up.
"""

from __future__ import annotations

from app.config import Settings, get_settings
from app.models import ClaimVerdict
from app.verify import answer_relevance

NO_RELEVANT_SOURCE = "no_relevant_source"
NO_CLAIMS = "model_produced_no_claims"
MODEL_DECLINED = "model_declined"
NO_SUPPORTED_CLAIMS = "no_supported_claims"
INSUFFICIENT_SUPPORT = "insufficient_support"
ANSWER_NOT_RELEVANT = "answer_not_relevant"


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
) -> Decision:
    settings = settings or get_settings()

    supported = [verdict for verdict in verdicts if verdict.supported]
    unsupported = [verdict for verdict in verdicts if not verdict.supported]

    # Nothing relevant was retrieved: abstain before the model output matters.
    if retrieval_confidence < settings.min_retrieval_confidence:
        return Decision("abstained", [NO_RELEVANT_SOURCE], [], unsupported + supported)

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
        relevance = answer_relevance(
            question,
            [verdict.text for verdict in supported]
            + [
                citation.quote
                for verdict in supported
                for citation in verdict.citations
                if citation.ok
            ],
        )
        if relevance < settings.min_answer_relevance:
            return Decision(
                "abstained", [ANSWER_NOT_RELEVANT], [], unsupported + supported
            )

    return Decision("answered", [], supported, unsupported, partial=bool(unsupported))
