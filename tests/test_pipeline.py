"""End to end: what a caller can and cannot be shown."""

from __future__ import annotations

from app.audit import AuditLog
from app.pipeline import DEFAULT_SCOPES, answer_question
from app.policy import NO_RELEVANT_SOURCE, PROVIDER_ERROR


def test_grounded_question_is_answered_with_resolvable_citations(settings, audit):
    result = answer_question(
        "Who approves an expense above 5,000 USD?", {"internal"}, settings, audit=audit
    )
    assert result.status == "answered"
    assert result.claims
    for claim in result.claims:
        assert claim.supported
        assert any(citation.ok for citation in claim.citations)
        for citation in claim.citations:
            if citation.ok:
                assert citation.start is not None and citation.end > citation.start


def test_unsupported_claims_never_reach_the_caller(settings, audit):
    result = answer_question(
        "How many years are trade confirmations kept for under the standard?",
        {"internal"},
        settings,
        audit=audit,
    )
    assert result.status == "abstained"
    assert result.claims == []
    assert result.answer_text == ""
    assert any("ungrounded_number" in verdict.reasons for verdict in result.dropped)


def test_restricted_answer_is_withheld_without_clearance(settings, audit):
    question = "What revenue did Acme Holdings report for the fourth quarter?"
    without = answer_question(question, {"internal"}, settings, audit=audit)
    assert without.status == "abstained"
    assert "412.6" not in without.model_dump_json()

    with_clearance = answer_question(
        question, {"internal", "restricted"}, settings, audit=audit
    )
    assert with_clearance.status == "answered"
    assert "412.6" in with_clearance.answer_text


def test_a_citation_to_a_withheld_chunk_is_refused(settings, audit):
    """The model tries to cite the restricted document it was not shown."""
    result = answer_question(
        "Who approved the Acme Q4 figure in the expense system?",
        {"internal"},
        settings,
        audit=audit,
    )
    assert result.status == "abstained"
    assert result.reasons == ["no_supported_claims"]
    assert result.dropped[0].citations[0].reason == "chunk_not_retrieved"


def test_unanswerable_question_abstains_without_calling_the_model(settings, audit):
    calls: list[str] = []

    class RecordingProvider:
        name = "recording"
        model = "recording"

        def complete(self, *, question: str, context: str) -> str:
            calls.append(question)
            return "{}"

    result = answer_question(
        "How many people work in the Zurich office?",
        {"internal"},
        settings,
        provider=RecordingProvider(),
        audit=audit,
    )
    assert result.status == "abstained"
    assert result.reasons == ["no_relevant_source"]
    assert calls == []


def test_every_request_writes_one_audit_record_and_the_chain_verifies(settings):
    audit = AuditLog(settings.audit_log_path)
    answer_question("Who approves an expense above 5,000 USD?", {"internal"}, settings, audit=audit)
    answer_question("How many people work in the Zurich office?", {"internal"}, settings, audit=audit)
    records = audit.read_all()
    assert len(records) == 2
    assert [record.status for record in records] == ["answered", "abstained"]
    assert audit.verify_chain()


def test_the_audit_record_keeps_what_the_answer_dropped(settings, audit):
    answer_question(
        "What is the CEO's travel budget under the expense policy?",
        {"internal"},
        settings,
        audit=audit,
    )
    record = audit.read_all()[-1]
    assert record.status == "abstained"
    assert record.served_claims == []
    assert record.dropped_claims
    assert record.retrieved
    assert record.prompt_version


def test_an_empty_clearance_set_is_not_the_default_clearance(settings, audit):
    """A requester cleared for nothing must not outrank one cleared for little.

    `None` means the caller did not state a clearance and the demo default
    applies. `set()` means the caller stated one and it is empty, which is
    what an entitlement lookup returns for an unknown or deprovisioned user. A
    truthiness test cannot tell those apart and would promote the second into
    the first: an empty clearance would be served the internal expense policy
    while `{"public"}` is refused, inverting the privilege ordering at the
    bottom of the lattice.

    Mutation check: write the coercion in `app/pipeline.py` as `scopes =
    set(scopes) if scopes else set(DEFAULT_SCOPES)` and this goes red, because
    the empty-set request answers.
    """
    question = "Who approves an expense above 5,000 USD?"

    unspecified = answer_question(question, None, settings, audit=audit)
    assert unspecified.status == "answered"
    assert unspecified.scopes == sorted(DEFAULT_SCOPES)

    cleared_for_nothing = answer_question(question, set(), settings, audit=audit)
    assert cleared_for_nothing.status == "abstained"
    assert cleared_for_nothing.reasons == [NO_RELEVANT_SOURCE]
    # Assert what must be absent, not only that it abstained: nothing was
    # retrieved and no scope was silently supplied on the requester's behalf.
    assert cleared_for_nothing.scopes == []
    assert cleared_for_nothing.retrieved == []

    # The ordering itself: cleared for nothing can never see more than
    # cleared for something.
    public_only = answer_question(question, {"public"}, settings, audit=audit)
    assert {record.chunk_id for record in cleared_for_nothing.retrieved} <= {
        record.chunk_id for record in public_only.retrieved
    }


class _ExplodingProvider:
    """A provider whose upstream is down. Everything else about it is real."""

    name = "exploding"
    model = "exploding-1"

    def complete(self, question: str, context: str) -> str:
        raise RuntimeError("upstream 503 from the model API")


def test_a_provider_failure_abstains_and_is_still_recorded(settings, audit, tmp_path):
    """The request that fails is the one a reader most wants in the log.

    `app/pipeline.py`'s docstring says the audit record is written for every
    request, answered or abstained, and that includes a request whose
    provider raised.

    Mutation check: remove the `except Exception` around `provider.complete` in
    `app/pipeline.py` and this goes red, because the RuntimeError propagates
    out of `answer_question` before any assertion.
    """
    log = AuditLog(tmp_path / "provider-failure.jsonl")

    result = answer_question(
        "Who approves an expense above 5,000 USD?",
        {"internal"},
        settings,
        provider=_ExplodingProvider(),
        audit=log,
    )

    assert result.status == "abstained"
    assert result.reasons[0] == PROVIDER_ERROR
    # The exception is reported, not swallowed: its type and message survive
    # into the reason and therefore into the audit record.
    assert "RuntimeError" in result.reasons[1]
    assert "upstream 503" in result.reasons[1]
    # Nothing was served, asserted as an absence and not inferred from the
    # status.
    assert result.claims == []

    records = log.read_all()
    assert len(records) == 1
    assert records[0].status == "abstained"
    assert records[0].reasons[0] == PROVIDER_ERROR
    assert log.verify_chain()


def test_the_caller_view_drops_the_withheld_text_and_keeps_the_reasons(settings, audit):
    """README says a caller gets reason codes only and the text stays in the log.

    The result object's `dropped` list carries the full text of every refused
    claim, so what a caller is shown, `--json` included, is the redacted view.
    An answer the system decided not to stand behind is exactly the text a
    caller should not be quoting.

    Mutation check: make `for_caller` return `self` and this goes red.
    """
    result = answer_question(
        "What revenue did Acme Holdings report for the fourth quarter?",
        {"public", "internal"}, settings, audit=audit,
    )
    assert result.dropped, "this question must withhold something, or the test proves nothing"
    assert any(claim.text for claim in result.dropped), "the raw result keeps the text"

    caller = result.for_caller()
    # Assert the absence, which is the claim.
    assert all(claim.text == "" for claim in caller.dropped)
    assert all(citation.quote == "" for claim in caller.dropped
               for citation in claim.citations)
    # The reasons and the coverage survive: a caller still has to be told that
    # something was withheld and why.
    assert all(claim.coverage is not None for claim in caller.dropped)
    # The served answer is untouched.
    assert [c.text for c in caller.claims] == [c.text for c in result.claims]
    # The original object is not mutated. Redaction must not destroy the
    # audit view held by the caller that asked for both.
    assert any(claim.text for claim in result.dropped)


def test_a_provider_failure_records_the_thresholds_and_corpus_it_saw(settings, tmp_path):
    """Even the failure path carries the auditor's fields.

    A record written on a provider outage is the record an operator reaches
    for first, and a shorter code path is the likeliest to forget a field.
    """
    log = AuditLog(tmp_path / "failed.jsonl")
    answer_question("Who approves an expense above 5,000 USD?", {"internal"},
                    settings, provider=_ExplodingProvider(), audit=log)
    record = log.read_all()[0]
    assert record.request_id and record.ts
    assert record.settings_digest == settings.digest()
    assert len(record.corpus_sha256) == 64
