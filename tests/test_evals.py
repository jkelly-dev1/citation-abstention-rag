"""The eval gate has to fail when the system regresses, or it proves nothing."""

from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]

import json
from dataclasses import replace

import app.evals.gate as gate_module
from app.audit import AuditLog
from app.evals.golden import GOLDEN_SET
from app.evals.runner import EvalReport, _check_offsets, gate_failures, run_evals
from app.llm import MockProvider


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


def test_the_gate_exit_code_says_pass_or_fail(settings, monkeypatch, capsys):
    """The exit code is the whole product of the gate, and nothing read it.

    With nothing calling `main()`, its `return 1` can become `return 0` and the
    suite stays green: the gate goes on printing "EVAL GATE FAILED" and naming
    the case, and exits 0, so the CI step passes while reporting its own
    failure. A separate demo step recomputing the same failures would still
    turn CI red, but that is a property of `run_demo.py`, not a test of the
    gate.

    `gate.py` does `from app.config import get_settings`, so it holds its own
    reference and patching `app.config.get_settings` would change nothing it
    calls. The binding patched here is the one the module actually resolves,
    and the regressed half below is the assertion a no-op patch cannot
    satisfy: with the real settings, numeric grounding is on and main() would
    return 0.

    Mutation check: change `return 1` to `return 0` in `gate.main()` and this
    goes red.
    """
    monkeypatch.setattr(gate_module, "get_settings", lambda: settings)
    assert gate_module.main() == 0
    assert "EVAL GATE PASSED" in capsys.readouterr().out

    regressed = settings.model_copy(update={"require_numeric_grounding": False})
    monkeypatch.setattr(gate_module, "get_settings", lambda: regressed)
    assert gate_module.main() == 1
    printed = capsys.readouterr().out
    assert "EVAL GATE FAILED" in printed
    assert "wrong-number" in printed


def test_every_threshold_branch_reports_its_own_breach(settings):
    """All five levers, each fired alone and each naming itself.

    In a real regression the per-case loop at the end of `gate_failures` fires
    alongside these and is strictly stronger, so deleting all five branches
    leaves a golden-set run green and makes the four EVAL_* settings in
    .env.example unable to change any outcome. A synthetic report with no
    outcomes is the only way to reach them on their own.

    Mutation check: delete any one of the five branches and this goes red
    naming that metric.
    """
    clean = EvalReport(
        outcomes=[],
        metrics={
            "citation_precision": 1.0,
            "model_citation_precision": 1.0,
            "unsupported_served": 0.0,
            "bad_offsets": 0.0,
            "abstention_recall": 1.0,
            "false_abstention_rate": 0.0,
        },
    )
    assert gate_failures(clean, settings) == []

    breached = EvalReport(
        outcomes=[],
        metrics={
            "citation_precision": 0.5,
            "model_citation_precision": 0.5,
            "unsupported_served": 3.0,
            "bad_offsets": 2.0,
            "abstention_recall": 0.5,
            "false_abstention_rate": 0.9,
        },
    )
    failures = gate_failures(breached, settings)
    assert len(failures) == 5, failures
    for metric in (
        "citation_precision",
        "unsupported_served",
        "bad_offsets",
        "abstention_recall",
        "false_abstention_rate",
    ):
        assert any(failure.startswith(metric) for failure in failures), metric


def test_bad_offsets_counts_a_span_that_does_not_reproduce_its_quote(settings, tmp_path):
    """The offset re-check fires. It was real but pinned by nothing.

    Replacing the body of `_check_offsets` with `return 0` leaves a golden-set
    run green, so the metric that re-reads every served citation from disk
    needs its own guard. Both directions are asserted: zero on the real result,
    non-zero once the recorded span is moved off the quote.

    Mutation check: `return 0` from `_check_offsets` and this goes red on the
    second assertion.
    """
    report = run_evals(settings, audit=AuditLog(tmp_path / "offsets.jsonl"))
    answered = [item for item in report.outcomes if item.result.status == "answered"]
    assert answered, "the golden set must answer something for this to measure"

    result = answered[0].result.model_copy(deep=True)
    assert _check_offsets(result) == 0

    shifted = 0
    for claim in result.claims:
        for citation in claim.citations:
            if citation.ok and citation.start is not None:
                citation.start += 1
                citation.end += 1
                shifted += 1
    assert shifted, "no served citation to shift"
    assert _check_offsets(result) == shifted


def test_a_case_expecting_the_wrong_reason_code_fails_the_gate(settings, tmp_path):
    """`expect_reason` is enforced, not decoration.

    gate.py says "the golden set pins which guardrail fires for each case", so
    the runner has to act on it. Disabling the comparison leaves a golden-set
    run green, which means every expected reason code could be wrong with
    nothing to notice.

    Mutation check: delete the `case.expect_reason not in result.reasons`
    branch in `run_case` and this goes red.
    """
    unanswerable = next(
        case for case in GOLDEN_SET if case.id == "unanswerable-headcount"
    )
    assert unanswerable.expect_reason == "no_relevant_source"

    mislabelled = replace(unanswerable, expect_reason="model_declined")
    report = run_evals(
        settings, cases=(mislabelled,), audit=AuditLog(tmp_path / "reason.jsonl")
    )
    failures = gate_failures(report, settings)
    assert any("expected reason model_declined" in failure for failure in failures), failures
    # The case still abstained, so nothing else could have produced the failure.
    assert report.outcomes[0].result.status == "abstained"


class _SecondCitationIsFabricated:
    """The mock, with one unverifiable citation added to every claim."""

    name = "mock"
    model = "mock-with-a-fabricated-second-citation"

    def complete(self, *, question: str, context: str) -> str:
        payload = json.loads(MockProvider().complete(question=question, context=context))
        for claim in payload.get("claims", []):
            citations = claim.setdefault("citations", [])
            first = citations[0]["chunk_id"] if citations else "expense-policy#s01"
            citations.append(
                {
                    "chunk_id": first,
                    "quote": "This sentence appears in no document in the corpus.",
                }
            )
        return json.dumps(payload)


def test_citation_precision_moves_when_a_served_claim_carries_a_bad_citation(
    settings, tmp_path
):
    """The metric is an invariant with the shipped mock, not a dead number.

    With the shipped mock every claim carries exactly one citation and a served
    claim is supported, so `citation_precision` is 1.000 by construction and
    `eval_min_citation_precision` asserts an invariant instead of
    discriminating between runs. This proves the metric is still live: a model
    that offers two citations for one claim, one of which does not verify,
    moves it, and the gate catches that. A metric that cannot move is
    indistinguishable from one that is broken.
    """
    clean = run_evals(settings, audit=AuditLog(tmp_path / "clean.jsonl"))
    assert clean.metrics["citation_precision"] == 1.0

    report = run_evals(
        settings,
        audit=AuditLog(tmp_path / "bad.jsonl"),
        provider=_SecondCitationIsFabricated(),
    )
    assert report.metrics["citation_precision"] < 1.0
    assert any(
        failure.startswith("citation_precision")
        for failure in gate_failures(report, settings)
    )


def test_model_citation_precision_counts_the_citations_that_were_dropped(
    settings, tmp_path
):
    """The metric that can actually report on the model.

    `citation_precision` never sees a dropped claim, so on a golden set built
    around four scripted fabrications it still reads 1.000. This one counts
    every citation the model produced, and must therefore be strictly lower.
    """
    report = run_evals(settings, audit=AuditLog(tmp_path / "model.jsonl"))
    assert report.metrics["citation_precision"] == 1.0
    assert report.metrics["model_citation_precision"] < 1.0
    assert any(item.result.dropped for item in report.outcomes)


def test_the_readme_number_checker_agrees_with_the_tree(monkeypatch):
    """The checker CI runs, run here, with its exit code asserted.

    Its whole product is the exit status, so calling it is the only way to
    know the status means anything. The second half asserts the other
    direction: pointed at a document missing every derived figure it must
    return 1, or a checker that always returns 0 satisfies the first half.

    It is imported with a plain `import`, not `importlib.util`, so the link
    between this test and the script is visible to anything that reads
    imports statically.
    """
    from pathlib import Path

    import scripts.check_readme_numbers as checker

    assert checker.main() == 0

    # Pointed at a document that carries none of the derived figures, it must
    # say so and return 1. A checker that cannot fail is not a check.
    monkeypatch.setattr(checker, "README", Path(__file__).resolve().parents[1] / "SECURITY.md")
    assert checker.main() == 1


def test_the_readme_number_checker_refuses_a_wrong_count_in_words(
    monkeypatch, tmp_path
):
    """A spelled-out count is checked as the phrase, not as a bare digit.

    The README says how many golden cases script a fabrication in words. A
    bare digit would be found somewhere in any README (every "5,000" contains
    a 5), so the checker would pass whatever the sentence said.

    Mutation check: make `fabrication_case_count` return the bare count and
    this goes red.
    """
    import re
    from pathlib import Path

    import scripts.check_readme_numbers as checker
    from app.evals.golden import GOLDEN_SET

    words = ("zero", "one", "two", "three", "four", "five", "six", "seven")
    count = sum("fabrication" in case.tags for case in GOLDEN_SET)
    sentence = r"(\s+cases\s+script\s+a\s+fabrication)"

    text = (Path(__file__).resolve().parents[1] / "README.md").read_text(
        encoding="utf-8")
    # Only the count word changes; every other line, the claims table
    # included, stays as it is, so nothing else can make the checker fail.
    altered_text, found = re.subn(
        rf"\b{words[count]}{sentence}", rf"{words[count - 1]}\1", text)
    assert found == 1
    altered = tmp_path / "README.md"
    altered.write_text(altered_text, encoding="utf-8")
    monkeypatch.setattr(checker, "README", altered)
    assert checker.main() == 1


def test_the_readme_number_checker_derives_the_held_out_miss_rate(
    monkeypatch, tmp_path
):
    """The held-out miss rate in the README is computed, not typed.

    Mutation check: drop `**held_out_figures()` from the checker's figures and
    this goes red, because a wrong rate in the README then passes.
    """
    from pathlib import Path

    import scripts.check_readme_numbers as checker
    from app.evals.runner import held_out_miss_rate

    rate = f"{held_out_miss_rate():.3f}"
    text = (Path(__file__).resolve().parents[1] / "README.md").read_text(
        encoding="utf-8")
    assert text.count(rate) == 1
    altered = tmp_path / "README.md"
    altered.write_text(text.replace(rate, "0.999"), encoding="utf-8")
    monkeypatch.setattr(checker, "README", altered)
    assert checker.main() == 1


def test_audit_show_reports_a_span_that_no_longer_resolves(tmp_path, monkeypatch, capsys):
    """The demo of the audit claim: resolve a record back to the source text.

    Tamper detection on the log is one half. The other half, and the one a
    compliance reader asks about, is a record resolved back to the document it
    cited.

    Mutation check: make `_record_resolves` return `(True, [])` and this goes
    red, because the edited corpus reports as resolving.
    """
    import shutil

    import app.cli as cli
    from app.config import Settings
    from app.corpus import cached_corpus

    corpus = tmp_path / "corpus"
    shutil.copytree("corpus", corpus)
    log_path = tmp_path / "show.jsonl"
    settings = Settings(corpus_dir=str(corpus), audit_log_path=str(log_path))
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    cached_corpus.cache_clear()

    assert cli.main(["ask", "Who approves an expense above 5,000 USD?"]) == 0
    capsys.readouterr()
    assert cli.main(["audit-show", "1"]) == 0
    assert "every served span reproduces its quote" in capsys.readouterr().out

    # Now change the cited document under the record.
    policy = corpus / "expense-policy.md"
    policy.write_text(
        policy.read_text(encoding="utf-8").replace(
            "Chief Financial Officer", "Chief Executive Officer", 1),
        encoding="utf-8")
    cached_corpus.cache_clear()

    assert cli.main(["audit-show", "1"]) == 1
    out = capsys.readouterr().out
    assert "MISMATCH" in out
    # Assert the reassuring sentence is absent as well as MISMATCH present.
    assert "every served span reproduces its quote" not in out
    cached_corpus.cache_clear()


def test_the_three_new_guardrail_cases_reach_the_model_and_fire(settings):
    """A gate that never produces a reason code cannot regress on it.

    negation_mismatch, model_output_unparseable and model_declined have unit
    tests. Unless the gate's own run reaches them, each branch could be
    removed with EVAL GATE PASSED still printing.

    The triggers have to retrieve. A scripted question that retrieves nothing
    abstains at `no_relevant_source` before the provider is ever called, and
    the script then demonstrates nothing at all. Each trigger below is
    therefore a phrase inside a question the retriever answers well.

    Mutation check: delete the `negate` script from `MockProvider.SCRIPTS` and
    this goes red.
    """
    from app.audit import AuditLog
    from app.pipeline import answer_question

    import tempfile
    from pathlib import Path as _P

    expected = {
        "Is written approval required above 5,000 USD?": "negation_mismatch",
        "How long are trade confirmations retained in full?": "model_output_unparseable",
        "Who approves an expense above 5,000 USD, precisely?": "model_declined",
    }
    with tempfile.TemporaryDirectory() as directory:
        audit = AuditLog(_P(directory) / "g.jsonl")
        for question, reason in expected.items():
            result = answer_question(question, {"public", "internal"}, settings,
                                     audit=audit)
            produced = set(result.reasons) | {
                r for claim in result.dropped for r in claim.reasons}
            assert result.status == "abstained", question
            assert reason in produced, f"{question} -> {produced}, wanted {reason}"


def test_the_held_out_miss_rate_is_measured_and_not_gated(settings):
    """It is reported so a reader sees what over-abstention costs.

    It must not become a gate while the questions share an author with the
    golden set. Whoever chose the questions should not also choose the bar.

    Mutation check: return 0.0 from `held_out_miss_rate` and this goes red.
    """
    from app.evals.paraphrases import HELD_OUT
    from app.evals.runner import gate_failures, held_out_miss_rate, run_evals

    rate = held_out_miss_rate(settings)
    # `0.0 < rate <= 1.0` is satisfied by `return True`, because True == 1.0
    # in Python. The rate must be a fraction of this set, so it has to be a
    # float and it has to be a multiple of 1/len(HELD_OUT).
    assert isinstance(rate, float) and not isinstance(rate, bool)
    assert len(HELD_OUT) >= 10
    misses = rate * len(HELD_OUT)
    assert abs(misses - round(misses)) < 1e-9, (
        f"{rate} is not a whole number of misses out of {len(HELD_OUT)}")
    assert 0.0 < rate < 1.0, (
        "a rate of exactly zero on a held-out set is the result this set "
        "exists to disbelieve, and exactly one would mean it answers nothing")

    # It is not a gate: no threshold reads it, so a bad number cannot fail the
    # build. Asserted by running the gate, not by reading the code.
    report = run_evals(settings)
    assert not any("held_out" in failure for failure in gate_failures(report, settings))
    assert "held_out_miss_rate" not in report.metrics


def test_the_adversarial_run_exit_code_says_pass_or_fail(monkeypatch, capsys):
    """CI reads the exit code, not the prose above it.

    `scripts/adversarial_run.py` returns 0 when nothing unsupported was served
    and 1 otherwise, and a CI step runs it, so the exit code is a verdict and
    not just termination.

    Mutation check: change the failing `return 1` to `return 0` and this goes
    red.
    """
    # A plain import, not importlib.spec_from_file_location. Both reach the
    # same module, and only a plain import is visible to a static reader.
    import scripts.adversarial_run as module

    assert module.main() == 0
    out = capsys.readouterr().out
    assert "unsupported_served       0" in out

    # It must be able to fail. Patch the binding the module resolves: it did
    # `from app.llm import HostileProvider`, so it holds its own reference and
    # patching app.llm would change nothing it calls.
    class _Pushover:
        name = "pushover"
        model = "pushover-v1"

        def complete(self, *, question, context):
            import json as _json
            return _json.dumps({"claims": [], "declined": False})

    monkeypatch.setattr(module, "HostileProvider", _Pushover)
    # With an adversary that attacks nothing, nothing is served unsupported
    # either, so a green run is not evidence on its own. The real failure
    # lever is the served-unsupported check itself.
    monkeypatch.setattr(module, "answer_question", _always_serves_unsupported)
    assert module.main() == 1
    assert "ADVERSARIAL RUN FAILED" in capsys.readouterr().out


def _always_serves_unsupported(question, scopes, settings, provider=None, audit=None):
    """A pipeline that serves a claim it knows is unsupported."""
    from app.models import AnswerResult, ClaimVerdict

    return AnswerResult(
        status="answered",
        question=question,
        scopes=sorted(scopes),
        claims=[ClaimVerdict(text="fabricated", supported=False, coverage=0.0,
                             citations=[], reasons=["no_verified_citation"])],
    )
