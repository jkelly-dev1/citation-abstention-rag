"""The audit log is tamper evident, or it is decoration."""

from __future__ import annotations

import json

import pytest

from app.audit import (
    GENESIS_HASH,
    AuditLog,
    AuditLogCorrupt,
    _hash_payload,
    verify_chain,
)
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


def _rechain(log: AuditLog, hmac_key: str | None) -> None:
    """Rewrite every hash in the file so the chain is internally consistent.

    This is the attack the unkeyed chain does not detect: not an in-place edit,
    which breaks every hash after it, but a full recomputation by someone who
    knows how the hash is built.
    """
    records = log.read_all()
    previous = GENESIS_HASH
    lines = []
    for record in records:
        record.prev_hash = previous
        record.record_hash = _hash_payload(record.payload_for_hash(), hmac_key)
        previous = record.record_hash
        lines.append(record.model_dump_json())
    log.path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_an_unkeyed_chain_accepts_an_editor_who_recomputes_it(tmp_path):
    """The documented limit, pinned so the README cannot quietly outgrow it.

    Unkeyed, "tamper evident" means evident against an edit made in place. An
    editor who changes a record and rebuilds the chain produces a file that
    verifies clean, because the digest is a plain SHA-256 anyone can compute.
    This test asserts that weakness deliberately. If a change makes the
    unkeyed path resist re-chaining, this goes red, and the claim in
    `app/audit.py` and the README has to be rewritten to match.
    """
    log = AuditLog(tmp_path / "plain.jsonl")
    for question in ("one", "two", "three"):
        log.append(_record(question))
    assert log.verify_chain()

    edited = log.read_all()
    edited[0].status = "abstained"
    log.path.write_text(
        "\n".join(record.model_dump_json() for record in edited) + "\n",
        encoding="utf-8",
    )
    assert not log.verify_chain(), "an in-place edit must still be caught"

    _rechain(log, hmac_key=None)
    assert log.verify_chain() is True
    assert log.read_all()[0].status == "abstained"


def test_a_keyed_chain_refuses_an_editor_who_lacks_the_key(tmp_path):
    """With AUDIT_HMAC_KEY set, re-chaining takes the key.

    Same attack, same code path, one difference: the digest is an HMAC. The
    editor can still rewrite every hash consistently, and the log is rejected,
    because the hashes they can compute are not the hashes this log verifies
    against.

    Mutation check: drop the `hmac_key` branch from `_hash_payload` so it
    always returns a plain sha256, and this goes red, because the re-chained
    log verifies.
    """
    log = AuditLog(tmp_path / "keyed.jsonl", hmac_key="a-key-that-lives-elsewhere")
    for question in ("one", "two", "three"):
        log.append(_record(question))
    assert log.verify_chain()

    _rechain(log, hmac_key=None)
    assert log.verify_chain() is False

    # The key is doing the work, not some accident of the rewrite: the same
    # rewrite performed with the key verifies.
    _rechain(log, hmac_key="a-key-that-lives-elsewhere")
    assert log.verify_chain() is True


def test_the_wrong_key_does_not_verify(tmp_path):
    written = AuditLog(tmp_path / "k.jsonl", hmac_key="right")
    written.append(_record("one"))
    assert written.verify_chain()
    assert AuditLog(tmp_path / "k.jsonl", hmac_key="wrong").verify_chain() is False
    assert AuditLog(tmp_path / "k.jsonl").verify_chain() is False


def test_a_corrupt_line_is_reported_as_a_broken_chain_not_a_traceback(tmp_path):
    """`audit-verify` exists to answer this question; a traceback is no answer.

    Unhandled, one malformed line would raise a bare `JSONDecodeError` out of
    `_last_hash` and a pydantic `ValidationError` out of `read_all`, so every
    later request would crash and `audit-verify` would print a traceback
    instead of AUDIT CHAIN BROKEN.

    Mutation check: remove the `except AuditLogCorrupt` in `verify_chain` and
    this goes red with the exception instead of False.
    """
    log = AuditLog(tmp_path / "corrupt.jsonl")
    log.append(_record("one"))
    log.append(_record("two"))
    assert log.verify_chain()

    with log.path.open("a", encoding="utf-8") as handle:
        handle.write("{not json at all\n")

    assert log.verify_chain() is False

    # Reading still raises, because a caller asking for the records must not
    # be handed a silently shortened list. What it raises names the line, not
    # a decoder's internals.
    with pytest.raises(AuditLogCorrupt) as caught:
        log.read_all()
    assert ":3:" in str(caught.value)

    with pytest.raises(AuditLogCorrupt):
        log.append(_record("four"))


def test_the_cli_reports_a_corrupt_log_instead_of_crashing(tmp_path, monkeypatch, capsys):
    """End to end, because the finding was about what the operator sees."""
    import app.cli as cli
    from app.config import Settings

    path = tmp_path / "cli.jsonl"
    log = AuditLog(path)
    log.append(_record("one"))
    with path.open("a", encoding="utf-8") as handle:
        handle.write("garbage\n")

    settings = Settings(audit_log_path=str(path))
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    assert cli.main(["audit-verify"]) == 1
    assert "AUDIT CHAIN BROKEN" in capsys.readouterr().err


def test_the_module_level_verify_chain_answers_for_a_path(tmp_path):
    """The convenience wrapper is a real entry point, so it is checked.

    `verify_chain(path)` lets a caller verify a log without constructing an
    `AuditLog`, and it takes the key too. Nothing else in the repository calls
    it, which is exactly why it needs a test: an untested wrapper can be
    replaced by `return True` with everything else still green, and a verifier
    that always says yes is worse than no verifier.

    Mutation check: make it `return True` and this goes red on the tampered
    case; make it ignore `hmac_key` and it goes red on the last.
    """
    path = tmp_path / "wrapper.jsonl"
    log = AuditLog(path)
    log.append(_record("one"))
    log.append(_record("two"))
    assert verify_chain(path) is True

    records = log.read_all()
    records[1].status = "abstained"
    path.write_text(
        "\n".join(record.model_dump_json() for record in records) + "\n",
        encoding="utf-8",
    )
    assert verify_chain(path) is False

    keyed = tmp_path / "wrapper-keyed.jsonl"
    AuditLog(keyed, hmac_key="k").append(_record("one"))
    assert verify_chain(keyed, hmac_key="k") is True
    assert verify_chain(keyed) is False


def test_audit_verify_on_a_missing_log_is_not_a_verified_empty_log(tmp_path, monkeypatch, capsys):
    """A mistyped path must not print the same sentence as a clean log.

    `read_all` treats a missing file as zero records and `verify_chain` is
    vacuously true over zero records, so without a guard `audit-verify` on a
    path that does not exist would print "audit chain OK: 0 record(s)" and
    exit 0, exactly what a genuinely verified empty log prints.

    Mutation check: remove the `if not log.path.exists()` guard in `app/cli.py`
    and this goes red with exit 0 and "audit chain OK".
    """
    import app.cli as cli
    from app.config import Settings

    missing = tmp_path / "nowhere" / "audit.jsonl"
    settings = Settings(audit_log_path=str(missing))
    # Patch the binding `app.cli` actually resolves, not `app.config.get_settings`:
    # cli.py did `from app.config import get_settings` and holds its own
    # reference, so patching the source module would change nothing it calls.
    monkeypatch.setattr(cli, "get_settings", lambda: settings)

    assert cli.main(["audit-verify"]) == 1
    captured = capsys.readouterr()
    assert "NO AUDIT LOG" in captured.err
    # Assert what must be absent: the reassuring sentence must not appear.
    assert "audit chain OK" not in captured.out
    # Checking for the file must not create it.
    assert not missing.exists()


def test_the_audit_record_says_when_under_what_rules_and_from_what_corpus(tmp_path):
    """"Decided when, and under what rules" is the auditor's first question.

    A record without a time, without the thresholds in force and without a
    corpus digest cannot answer it, however faithfully it records the decision.

    Mutation check: drop `settings_digest` from the TraceRecord construction in
    `app/pipeline.py` and this goes red.
    """
    from app.config import Settings
    from app.pipeline import answer_question

    log = AuditLog(tmp_path / "rich.jsonl")
    settings = Settings(corpus_dir="corpus", audit_log_path=str(tmp_path / "rich.jsonl"))
    answer_question("Who approves an expense above 5,000 USD?", {"internal"},
                    settings, audit=log)

    record = log.read_all()[0]
    assert record.request_id and len(record.request_id) == 32
    assert record.ts.endswith("+00:00"), "the timestamp must be explicit UTC"
    assert record.settings_digest == settings.digest()
    assert len(record.corpus_sha256) == 64
    # The model's reply is fingerprinted, not stored: the record must carry its
    # size and digest and must not carry the text.
    assert record.raw_output_length > 0
    assert len(record.raw_output_sha256) == 64
    # Assert the absence: the reply text itself must not be in the record.
    assert "Chief Financial Officer" not in record.raw_output_sha256
    assert not hasattr(record, "raw_output")


def test_the_settings_digest_moves_with_a_threshold_and_not_with_a_path():
    """The digest fingerprints the rules a decision was made under.

    A loosened threshold must change it, or two records decided under
    different rules look identical. A moved log file or a new HMAC key must
    not, because neither changes a decision.

    Mutation check: drop the `min_` prefix from the selection in
    `Settings.digest` and the first assertion goes red.
    """
    from app.config import Settings

    base = Settings()
    assert base.model_copy(update={"min_claim_coverage": 0.1}).digest() != base.digest()
    assert base.model_copy(update={"top_k": base.top_k + 1}).digest() != base.digest()
    assert base.model_copy(
        update={"audit_log_path": "elsewhere.jsonl",
                "audit_hmac_key": "a-new-key"}).digest() == base.digest()


def test_two_writers_cannot_fork_the_chain(tmp_path):
    """Without a lock both readers see the same head and both append to it.

    The file then holds two records claiming the same predecessor and
    `verify_chain` rejects everything after them. Nothing here is concurrent,
    but the first caller to put it behind a web handler would be.

    Mutation check: remove the `flock` call in `AuditLog.append` and this goes
    red, because the chain forks and verification fails.
    """
    import threading

    path = tmp_path / "concurrent.jsonl"
    errors: list[BaseException] = []

    def writer(n: int) -> None:
        try:
            log = AuditLog(path)
            for i in range(25):
                log.append(_record(f"writer {n} record {i}"))
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    # Enough contention that the window is not a coin toss. The unflushed
    # window is between a writer releasing the lock and `with` closing the
    # handle, which is short. With four writers, removing the flush fails this
    # test only sometimes; with eight it fails reliably.
    threads = [threading.Thread(target=writer, args=(n,)) for n in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors, errors
    log = AuditLog(path)
    assert len(log.read_all()) == 8 * 25
    assert log.verify_chain(), "concurrent appends forked the chain"


def test_an_older_record_schema_is_not_reported_as_tampering(tmp_path, monkeypatch, capsys):
    """A schema boundary is not a break, and saying so matters.

    A record's hash covers its whole payload, so adding a field to
    `TraceRecord` changes every hash and a log written before the change stops
    recomputing. Reporting that as "AUDIT CHAIN BROKEN" would send an
    operator looking for an intruder who was never there, on the strength of
    records this build cannot check.

    Mutation check: delete the `foreign` branch in `app/cli.py` and this goes
    red, because the older log reports as BROKEN.
    """
    import json

    import app.cli as cli
    from app.config import Settings

    path = tmp_path / "old-schema.jsonl"
    log = AuditLog(path)
    log.append(_record("one"))

    # Rewrite it as a version-1 record: no `schema_version`, and a hash that
    # was correct for the payload WITHOUT the fields this build adds.
    raw = json.loads(path.read_text(encoding="utf-8").strip())
    for field in ("schema_version", "request_id", "ts", "settings_digest",
                  "corpus_sha256", "raw_output_sha256", "raw_output_length"):
        raw.pop(field, None)
    path.write_text(json.dumps(raw) + "\n", encoding="utf-8")

    settings = Settings(audit_log_path=str(path))
    monkeypatch.setattr(cli, "get_settings", lambda: settings)

    assert cli.main(["audit-verify"]) == 1
    err = capsys.readouterr().err
    assert "OLDER RECORD SCHEMA" in err
    assert "not evidence of tampering" in err
    # Assert the absence: the phrase that sends someone hunting must not appear.
    assert "AUDIT CHAIN BROKEN" not in err


def test_a_real_edit_is_still_reported_as_a_broken_chain(tmp_path, monkeypatch, capsys):
    """The schema branch must not become a way of excusing every failure."""
    import json

    import app.cli as cli
    from app.config import Settings

    path = tmp_path / "tampered.jsonl"
    log = AuditLog(path)
    log.append(_record("one"))
    log.append(_record("two"))

    raw = [json.loads(line) for line in path.read_text(encoding="utf-8").strip().splitlines()]
    raw[0]["status"] = "answered-tampered"
    path.write_text("\n".join(json.dumps(r) for r in raw) + "\n", encoding="utf-8")

    settings = Settings(audit_log_path=str(path))
    monkeypatch.setattr(cli, "get_settings", lambda: settings)

    assert cli.main(["audit-verify"]) == 1
    err = capsys.readouterr().err
    assert "AUDIT CHAIN BROKEN" in err
    assert "OLDER RECORD SCHEMA" not in err
