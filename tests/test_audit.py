"""The audit log is tamper evident, or it is decoration."""

from __future__ import annotations

import json

from app.audit import GENESIS_HASH, AuditLog
from app.models import TraceRecord


def _record(question: str) -> TraceRecord:
    return TraceRecord(
        question=question,
        scopes=["internal"],
        provider="mock",
        model="mock-deterministic-v1",
        prompt_version="test/v1",
        retrieval_confidence=0.9,
        status="answered",
    )


def test_records_chain_to_their_predecessor(audit):
    first = audit.append(_record("one"))
    second = audit.append(_record("two"))
    assert first.prev_hash == GENESIS_HASH
    assert second.prev_hash == first.record_hash
    assert audit.verify_chain()


def test_editing_a_past_record_breaks_the_chain(audit):
    audit.append(_record("one"))
    audit.append(_record("two"))
    audit.append(_record("three"))

    lines = audit.path.read_text(encoding="utf-8").splitlines()
    tampered = json.loads(lines[0])
    tampered["status"] = "abstained"
    lines[0] = json.dumps(tampered)
    audit.path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    assert audit.verify_chain() is False


def test_reordering_records_breaks_the_chain(audit):
    audit.append(_record("one"))
    audit.append(_record("two"))
    lines = audit.path.read_text(encoding="utf-8").splitlines()
    audit.path.write_text("\n".join(reversed(lines)) + "\n", encoding="utf-8")
    assert audit.verify_chain() is False


def test_deleting_a_middle_record_breaks_the_chain(audit):
    audit.append(_record("one"))
    audit.append(_record("two"))
    audit.append(_record("three"))
    lines = audit.path.read_text(encoding="utf-8").splitlines()
    audit.path.write_text("\n".join([lines[0], lines[2]]) + "\n", encoding="utf-8")
    assert audit.verify_chain() is False


def test_prev_hash_is_covered_by_the_record_hash(audit):
    """Mutation check: drop prev_hash from payload_for_hash and this fails.

    If the link were not hashed, a record could be moved to a different
    position in the file without invalidating its own hash.
    """
    first = audit.append(_record("one"))
    second = audit.append(_record("two"))
    forged = second.model_copy(update={"prev_hash": GENESIS_HASH})
    from app.audit import compute_record_hash

    assert compute_record_hash(forged) != second.record_hash
    assert first.record_hash != GENESIS_HASH


def test_reading_an_absent_log_is_empty_and_valid(tmp_path):
    log = AuditLog(tmp_path / "missing" / "audit.jsonl")
    assert log.read_all() == []
    assert log.verify_chain()
