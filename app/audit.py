"""Append-only, hash-chained audit log.

Each record is one JSON line. Before writing, `prev_hash` is set to the
previous record's `record_hash` and this record's hash is computed over its
canonical payload, which includes `prev_hash`. Editing, reordering, or removing
a past record therefore breaks every hash after it, and `verify_chain` reports
the break instead of quietly accepting the file.

What the chain does and does not prove depends entirely on the key.

Without `AUDIT_HMAC_KEY`, the digest is a plain SHA-256 that anyone can
compute. The chain then detects an edit made in place, because every hash
after the edited record stops matching. It does not detect an editor who
changes a record and recomputes the whole chain from that point on: that log
verifies clean. Unkeyed hashing gives integrity against corruption and accident, not
against an adversary, and "the attacker did not bother to re-chain" is an
assumption about diligence rather than a threat model.

With `AUDIT_HMAC_KEY` set, the digest is an HMAC and re-chaining requires the
key. An editor who has the log but not the key cannot produce hashes that
verify. The key must live somewhere the log writer cannot be rewritten from
(an environment secret, a KMS, a separate host), or it proves nothing it did
not already prove. That placement is outside this repository's scope, so the
default is off and no key is checked in. Anchoring the head hash somewhere
append-only is the other standard answer and is not implemented here.
"""

from __future__ import annotations

import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path

from app.models import RECORD_SCHEMA_VERSION, TraceRecord

GENESIS_HASH = "0" * 64


class AuditLogCorrupt(ValueError):
    """A line of the log is not a readable record.

    Raised with the file and line number in place of a bare `JSONDecodeError`
    or `ValidationError` from somewhere inside pydantic, because the
    operator's next question is always which line.
    """


def _hash_payload(payload: dict, hmac_key: str | None = None) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    encoded = canonical.encode("utf-8")
    if hmac_key:
        return hmac.new(hmac_key.encode("utf-8"), encoded, hashlib.sha256).hexdigest()
    return hashlib.sha256(encoded).hexdigest()


def compute_record_hash(record: TraceRecord, hmac_key: str | None = None) -> str:
    return _hash_payload(record.payload_for_hash(), hmac_key)


class AuditLog:
    def __init__(self, path: str | Path, hmac_key: str | None = None) -> None:
        self.path = Path(path)
        self.hmac_key = hmac_key or None
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _lines(self):
        """Yield (line number, stripped text) for every non-empty line."""
        with self.path.open("r", encoding="utf-8") as handle:
            for number, line in enumerate(handle, start=1):
                line = line.strip()
                if line:
                    yield number, line

    def _last_hash(self) -> str:
        if not self.path.exists():
            return GENESIS_HASH
        last = GENESIS_HASH
        for number, line in self._lines():
            try:
                last = json.loads(line)["record_hash"]
            except (json.JSONDecodeError, TypeError, KeyError) as exc:
                # Appending after an unreadable tail would chain the new record
                # onto a hash nobody can verify, so refuse and say where.
                raise AuditLogCorrupt(
                    f"{self.path}:{number}: cannot read the previous record, so a "
                    f"new record cannot be chained onto it ({exc})"
                ) from exc
        return last

    def append(self, record: TraceRecord) -> TraceRecord:
        """Append one record, under an exclusive lock on the log itself.

        Without the lock, two writers fork the chain. Both read the same head
        hash, both compute `prev_hash` from it, and both append: the file then
        holds two records claiming the same predecessor, and `verify_chain`
        rejects the whole log from that point on. Nothing in this repository
        is concurrent, but the first caller to put it behind a web handler
        would be.

        The lock is taken before the head is read. Reading the head and then
        locking is the same race with a smaller window.

        `flock` is advisory and POSIX-only. A writer that does not ask is not
        stopped, and this is not a substitute for a real append-only store.
        SECURITY.md says which properties the log does and does not have.
        """
        self.path.touch(exist_ok=True)
        with self.path.open("r+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                # Every write path stamps the schema, not just the pipeline.
                # A record built directly (by a test, a script, another
                # caller) would otherwise be indistinguishable from one
                # written before the field existed.
                record.schema_version = (
                    record.schema_version or RECORD_SCHEMA_VERSION)
                record.prev_hash = self._last_hash()
                record.record_hash = compute_record_hash(record, self.hmac_key)
                handle.seek(0, 2)
                handle.write(record.model_dump_json() + "\n")
                # Flush before releasing the lock. Python buffers the write,
                # and `with` flushes on close, which happens after the unlock
                # below. The next writer would then take the lock, read a head
                # hash that does not yet include this record, and append at a
                # stale offset, losing records and breaking the chain.
                # tests/test_audit.py runs eight concurrent writers against
                # this.
                handle.flush()
                os.fsync(handle.fileno())
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return record

    def read_all(self) -> list[TraceRecord]:
        if not self.path.exists():
            return []
        records: list[TraceRecord] = []
        for number, line in self._lines():
            try:
                records.append(TraceRecord.model_validate_json(line))
            except ValueError as exc:
                raise AuditLogCorrupt(f"{self.path}:{number}: {exc}") from exc
        return records

    def first_failing_record(self) -> tuple[int, "TraceRecord | None"]:
        """-> (1-based position, record) of the first record that does not verify.

        Which record fails decides what to tell the operator, and a log can
        hold both a legacy record and a tampered one. Answering "is there a
        legacy record anywhere" instead would let a real edit hide behind an
        old one, with the schema message excusing exactly the thing the chain
        exists to catch.
        """
        try:
            records = self.read_all()
        except AuditLogCorrupt:
            return (0, None)
        previous = GENESIS_HASH
        for index, record in enumerate(records, start=1):
            if record.prev_hash != previous:
                return (index, record)
            if compute_record_hash(record, self.hmac_key) != record.record_hash:
                return (index, record)
            previous = record.record_hash
        return (0, None)

    def verify_chain(self) -> bool:
        """True only if every record hashes correctly and links its predecessor.

        A line that cannot be parsed is a broken chain, not an error to raise
        at the caller: `audit-verify` exists to answer this question, and a
        traceback is not an answer. The exception is caught here and reported
        as the False it is.
        """
        try:
            records = self.read_all()
        except AuditLogCorrupt:
            return False
        previous = GENESIS_HASH
        for record in records:
            if record.prev_hash != previous:
                return False
            if compute_record_hash(record, self.hmac_key) != record.record_hash:
                return False
            previous = record.record_hash
        return True


def verify_chain(path: str | Path, hmac_key: str | None = None) -> bool:
    return AuditLog(path, hmac_key).verify_chain()
