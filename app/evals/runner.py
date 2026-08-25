"""Eval runner and metrics.

The metrics are chosen so that a system which answers everything cannot score
well. `unsupported_served` and `bad_offsets` are liability metrics: both must
be zero. `false_abstention_rate` is the cost of caution and is the one metric
that is allowed to be non-zero, because over-abstention is a usability problem
rather than a correctness one.

`bad_offsets` re-checks each served citation against the source file on disk,
using only the offsets recorded in the result. It does not reuse the retrieval
path, so a bug that made verification agree with itself would still be caught.

The gate runs against the deterministic mock provider on purpose. It measures
the pipeline, not the model of the day: the expected reason codes encode which
guardrail should fire for each case, and a live model would move them around
run to run. Real model behavior is captured in SAMPLE_RUN.md instead.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from app.audit import AuditLog
from app.config import Settings, get_settings
from app.evals.golden import GOLDEN_SET, GoldenCase
from app.llm import LLMProvider, MockProvider
from app.models import AnswerResult
from app.pipeline import answer_question
from app.verify import normalize


@dataclass
class CaseOutcome:
    case: GoldenCase
    result: AnswerResult
    passed: bool
    failures: list[str] = field(default_factory=list)


@dataclass
class EvalReport:
    outcomes: list[CaseOutcome]
    metrics: dict[str, float]

    @property
    def passed(self) -> bool:
        return all(outcome.passed for outcome in self.outcomes)


def _check_offsets(result: AnswerResult) -> int:
    """Count served citations whose recorded span does not reproduce the quote."""
    bad = 0
    for claim in result.claims:
        for citation in claim.citations:
            if not citation.ok:
                continue
            if not citation.source_path or citation.start is None or citation.end is None:
                bad += 1
                continue
            raw = Path(citation.source_path).read_text(encoding="utf-8")
            span = raw[citation.start : citation.end]
            if normalize(span) != normalize(citation.quote):
                bad += 1
    return bad


def run_case(
    case: GoldenCase,
    settings: Settings,
    audit: AuditLog | None = None,
    provider: LLMProvider | None = None,
) -> CaseOutcome:
    result = answer_question(
        case.question,
        set(case.scopes),
        settings,
        provider=provider or MockProvider(),
        audit=audit,
    )
    failures: list[str] = []

    if case.expect == "answer" and result.status != "answered":
        failures.append(f"expected an answer, abstained ({', '.join(result.reasons)})")
    if case.expect == "abstain" and result.status != "abstained":
        failures.append("expected abstention, answered")
    if (
        case.expect == "abstain"
        and case.expect_reason
        and result.status == "abstained"
        and case.expect_reason not in result.reasons
    ):
        failures.append(
            f"expected reason {case.expect_reason}, got {', '.join(result.reasons) or 'none'}"
        )
    if case.expect == "answer" and case.must_cite_doc and result.status == "answered":
        cited = {
            citation.doc_id
            for claim in result.claims
            for citation in claim.citations
            if citation.ok
        }
        if case.must_cite_doc not in cited:
            failures.append(
                f"expected a citation to {case.must_cite_doc}, cited {sorted(cited)}"
            )
    if any(not claim.supported for claim in result.claims):
        failures.append("an unsupported claim was served")
    bad_offsets = _check_offsets(result)
    if bad_offsets:
        failures.append(f"{bad_offsets} served citation(s) have offsets that do not match")

    return CaseOutcome(case=case, result=result, passed=not failures, failures=failures)


def run_evals(
    settings: Settings | None = None,
    cases: tuple[GoldenCase, ...] = GOLDEN_SET,
    audit: AuditLog | None = None,
    provider: LLMProvider | None = None,
) -> EvalReport:
    """Run the golden set. Defaults to the mock so the gate is reproducible."""
    settings = settings or get_settings()
    provider = provider or MockProvider()
    outcomes = [run_case(case, settings, audit, provider) for case in cases]

    served_citations = 0
    verified_citations = 0
    unsupported_served = 0
    bad_offsets = 0
    for outcome in outcomes:
        for claim in outcome.result.claims:
            if not claim.supported:
                unsupported_served += 1
            for citation in claim.citations:
                served_citations += 1
                if citation.ok:
                    verified_citations += 1
        bad_offsets += _check_offsets(outcome.result)

    should_abstain = [item for item in outcomes if item.case.expect == "abstain"]
    should_answer = [item for item in outcomes if item.case.expect == "answer"]
    abstained_correctly = sum(
        1 for item in should_abstain if item.result.status == "abstained"
    )
    abstained_wrongly = sum(
        1 for item in should_answer if item.result.status == "abstained"
    )

    metrics = {
        "cases": float(len(outcomes)),
        "cases_passed": float(sum(1 for item in outcomes if item.passed)),
        "citation_precision": (
            verified_citations / served_citations if served_citations else 1.0
        ),
        "unsupported_served": float(unsupported_served),
        "bad_offsets": float(bad_offsets),
        "abstention_recall": (
            abstained_correctly / len(should_abstain) if should_abstain else 1.0
        ),
        "false_abstention_rate": (
            abstained_wrongly / len(should_answer) if should_answer else 0.0
        ),
    }
    return EvalReport(outcomes=outcomes, metrics=metrics)


def gate_failures(report: EvalReport, settings: Settings | None = None) -> list[str]:
    """Threshold breaches. An empty list means the gate passes."""
    settings = settings or get_settings()
    metrics = report.metrics
    failures: list[str] = []
    if metrics["citation_precision"] < settings.eval_min_citation_precision:
        failures.append(
            f"citation_precision {metrics['citation_precision']:.3f} < "
            f"{settings.eval_min_citation_precision}"
        )
    if metrics["unsupported_served"] > settings.eval_max_unsupported_served:
        failures.append(
            f"unsupported_served {int(metrics['unsupported_served'])} > "
            f"{settings.eval_max_unsupported_served}"
        )
    if metrics["bad_offsets"] > 0:
        failures.append(f"bad_offsets {int(metrics['bad_offsets'])} > 0")
    if metrics["abstention_recall"] < settings.eval_min_abstention_recall:
        failures.append(
            f"abstention_recall {metrics['abstention_recall']:.3f} < "
            f"{settings.eval_min_abstention_recall}"
        )
    if metrics["false_abstention_rate"] > settings.eval_max_false_abstention_rate:
        failures.append(
            f"false_abstention_rate {metrics['false_abstention_rate']:.3f} > "
            f"{settings.eval_max_false_abstention_rate}"
        )
    for outcome in report.outcomes:
        for failure in outcome.failures:
            failures.append(f"case {outcome.case.id}: {failure}")
    return failures
