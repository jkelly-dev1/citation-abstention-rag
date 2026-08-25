"""The eval gate has to fail when the system regresses, or it proves nothing."""

from __future__ import annotations

from app.audit import AuditLog
from app.evals.golden import GOLDEN_SET
from app.evals.runner import gate_failures, run_evals


def test_golden_set_passes_the_gate(settings, audit):
    report = run_evals(settings, audit=audit)
    assert gate_failures(report, settings) == []
    assert report.passed


def test_golden_set_covers_both_answering_and_abstaining():
    expectations = {case.expect for case in GOLDEN_SET}
    assert expectations == {"answer", "abstain"}
    assert sum(1 for case in GOLDEN_SET if case.expect == "abstain") >= 5


def test_gate_fails_when_numeric_grounding_is_disabled(settings, tmp_path):
    """Mutation check on the eval gate itself.

    Turning off numeric grounding makes the system serve a claim that quotes a
    real sentence while stating a different number. The gate must catch that,
    and the failure must name the case.
    """
    regressed = settings.model_copy(update={"require_numeric_grounding": False})
    report = run_evals(regressed, audit=AuditLog(tmp_path / "regressed.jsonl"))
    failures = gate_failures(report, regressed)
    assert failures
    assert any("wrong-number" in failure for failure in failures)


def test_gate_fails_when_the_scope_filter_is_widened(settings, tmp_path):
    """Widening a requester's clearance must show up as a golden-set failure."""
    widened = tuple(
        case.__class__(
            id=case.id,
            question=case.question,
            expect=case.expect,
            scopes=frozenset({"public", "internal", "restricted"}),
            expect_reason=case.expect_reason,
            must_cite_doc=case.must_cite_doc,
            note=case.note,
            tags=case.tags,
        )
        for case in GOLDEN_SET
    )
    report = run_evals(settings, cases=widened, audit=AuditLog(tmp_path / "widened.jsonl"))
    failures = gate_failures(report, settings)
    assert any("revenue-without-clearance" in failure for failure in failures)


def test_gate_fails_when_the_abstention_thresholds_are_removed(settings, tmp_path):
    permissive = settings.model_copy(
        update={
            "min_retrieval_confidence": 0.0,
            "min_supported_ratio": 0.0,
            "min_answer_relevance": 0.0,
            "min_claim_coverage": 0.0,
        }
    )
    report = run_evals(permissive, audit=AuditLog(tmp_path / "permissive.jsonl"))
    failures = gate_failures(report, permissive)
    assert failures
    assert report.metrics["abstention_recall"] < 1.0


def test_eval_reruns_are_deterministic(settings, tmp_path):
    first = run_evals(settings, audit=AuditLog(tmp_path / "a.jsonl"))
    second = run_evals(settings, audit=AuditLog(tmp_path / "b.jsonl"))
    assert first.metrics == second.metrics
