"""End to end: what a caller can and cannot be shown."""

from __future__ import annotations

from app.audit import AuditLog
from app.pipeline import answer_question


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
