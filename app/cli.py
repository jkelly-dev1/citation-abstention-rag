"""Command line entry point.

    python -m app.cli ask "How long are trade confirmations retained?"
    python -m app.cli ask "What was Acme's Q4 revenue?" --scope internal,restricted
    python -m app.cli audit-verify
"""

from __future__ import annotations

import argparse
import json
import sys

from pathlib import Path

from app.audit import AuditLog, AuditLogCorrupt
from app.config import get_settings
from app.corpus import UNLABELED_SCOPE, cached_corpus, corpus_digest
from app.models import RECORD_SCHEMA_VERSION, AnswerResult
from app.pipeline import DEFAULT_SCOPES, answer_question
from app.verify import normalize


def format_result(result: AnswerResult) -> str:
    lines = [
        f"question   : {result.question}",
        f"scopes     : {', '.join(result.scopes)}",
        f"provider   : {result.provider} ({result.model})",
        f"retrieval  : confidence {result.retrieval_confidence:.2f}"
        + (
            "  [" + ", ".join(item.chunk_id for item in result.retrieved) + "]"
            if result.retrieved
            else "  [no chunks]"
        ),
        f"status     : {result.status.upper()}"
        + (f"  ({', '.join(result.reasons)})" if result.reasons else ""),
    ]
    if result.status == "answered":
        lines.append("answer     :")
        for claim in result.claims:
            lines.append(f"  - {claim.text}")
            for citation in claim.citations:
                if citation.ok:
                    lines.append(
                        f"      source: {citation.doc_id} chars {citation.start}-{citation.end}"
                    )
                    # A quote copied across a line break in the source arrives
                    # with the newline in it. Verification normalizes that
                    # away; the display should too.
                    flattened = " ".join(citation.quote.split())
                    lines.append(f'      quote : "{flattened}"')
        if result.partial:
            lines.append(
                f"  note: {len(result.dropped)} unsupported claim(s) removed before answering"
            )
    # Withheld claims are shown here because this is a demo and a reader has to
    # see why the system refused. A production caller would get the reason
    # codes only; the claim text stays in the audit log.
    for dropped in result.dropped:
        lines.append(f"withheld   : {dropped.text}")
        reasons = ", ".join(dropped.reasons) or "verified, but the answer was not served"
        lines.append(f"             reasons: {reasons} (coverage {dropped.coverage:.2f})")
    return "\n".join(lines)


def _record_resolves(record) -> tuple[bool, list[str]]:
    """-> (every served span still reproduces its quote, one line each).

    This re-reads the file from disk. The record names a path, a start and an
    end; this opens that path at that span and compares it against the quote
    the record stored. A mismatch means the citation no longer resolves:
    either the document changed under it, or the offsets were wrong when
    written. An auditor cannot tell those apart from the record alone, so the
    line says which it is where it can.
    """
    lines: list[str] = []
    ok = True
    for claim in record.served_claims:
        for citation in claim.citations:
            if not citation.ok:
                continue
            path = Path(citation.source_path or "")
            if not path.is_file():
                ok = False
                lines.append(f"  UNRESOLVABLE  {citation.chunk_id}: no file at {path}")
                continue
            body = path.read_text(encoding="utf-8")
            span = body[citation.start : citation.end]
            if normalize(span) == normalize(citation.quote):
                lines.append(f"  resolves      {citation.chunk_id} "
                             f"{path.name}[{citation.start}:{citation.end}]")
            else:
                ok = False
                lines.append(
                    f"  MISMATCH      {citation.chunk_id} "
                    f"{path.name}[{citation.start}:{citation.end}]\n"
                    f"      record : {citation.quote[:70]!r}\n"
                    f"      on disk: {span[:70]!r}")
    if not lines:
        lines.append("  (this record served no citations)")
    return ok, lines


def format_record(record, settings) -> str:
    """One audit record, with every served citation resolved against disk."""
    resolved_ok, lines = _record_resolves(record)
    out = [
        f"request    : {record.request_id or '(none recorded)'}",
        f"at         : {record.ts or '(none recorded)'}",
        f"question   : {record.question}",
        f"status     : {record.status}"
        + (f"  reasons: {', '.join(record.reasons)}" if record.reasons else ""),
        f"provider   : {record.provider} ({record.model})",
        f"thresholds : {record.settings_digest or '(none recorded)'}"
        + ("  SAME AS NOW" if record.settings_digest == settings.digest()
           else "  DIFFERENT FROM THE THRESHOLDS IN FORCE NOW"),
        f"corpus     : {record.corpus_sha256[:16] or '(none recorded)'}",
    ]
    if record.raw_output_length:
        out.append(f"model reply: {record.raw_output_length} chars, "
                   f"sha256 {record.raw_output_sha256[:16]}")
    out.append("citations  :")
    out.extend(lines)
    out.append("resolved   : " + ("every served span reproduces its quote"
                                  if resolved_ok else
                                  "AT LEAST ONE SPAN DOES NOT RESOLVE"))
    return "\n".join(out)


def _corpus_check(settings) -> int:
    """List every document, and fail if one is withheld from everybody.

    A document with no `scope:` line takes UNLABELED_SCOPE, which no requester
    holds. That is correct and silent: the only symptom is that questions it
    should answer come back `no_relevant_source`, and the next reader debugs
    retrieval instead of front matter.
    """
    chunks = list(cached_corpus(settings.corpus_dir))
    if not chunks:
        print(f"NO CORPUS DOCUMENTS under {settings.corpus_dir}", file=sys.stderr)
        return 1
    by_doc: dict[str, list] = {}
    for chunk in chunks:
        by_doc.setdefault(chunk.doc_id, []).append(chunk)
    withheld = []
    print(f"{'doc_id':<32} {'scope':<12} {'ver':<6} {'chunks':>6}  sha256")
    for doc_id, items in sorted(by_doc.items()):
        first = items[0]
        flag = ""
        if first.scope == UNLABELED_SCOPE:
            withheld.append(doc_id)
            flag = "  <- NO scope: LINE, WITHHELD FROM EVERY REQUESTER"
        print(f"{doc_id:<32} {first.scope:<12} {first.version or '-':<6} "
              f"{len(items):>6}  {first.doc_sha256[:12]}{flag}")
    print(f"\n{len(by_doc)} document(s), {len(chunks)} chunk(s), "
          f"corpus digest {corpus_digest(chunks)[:16]}")
    if withheld:
        print(f"\n{len(withheld)} document(s) fail closed and answer nobody: "
              + ", ".join(withheld), file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="citation-abstention-rag")
    sub = parser.add_subparsers(dest="command", required=True)

    ask = sub.add_parser("ask", help="Answer a question, or abstain with a reason")
    ask.add_argument("question")
    ask.add_argument(
        "--scope",
        default=",".join(sorted(DEFAULT_SCOPES)),
        help="Comma separated scopes the requester is cleared for",
    )
    ask.add_argument("--json", action="store_true", help="Emit the full result as JSON")

    sub.add_parser("audit-verify", help="Verify the audit log hash chain")

    show = sub.add_parser(
        "audit-show",
        help="Resolve one audit record back to the source text it cited",
    )
    show.add_argument("index", type=int, help="1-based record number")

    sub.add_parser(
        "corpus-check",
        help="List every corpus document and flag any that fails closed",
    )

    args = parser.parse_args(argv)
    settings = get_settings()

    try:
        return _dispatch(args, settings)
    except AuditLogCorrupt as exc:
        # One malformed line otherwise poisons every later request with a
        # traceback out of json or pydantic. Name the line and exit nonzero.
        print(f"AUDIT LOG UNREADABLE: {exc}", file=sys.stderr)
        return 1


def _dispatch(args: argparse.Namespace, settings) -> int:
    if args.command == "ask":
        scopes = {scope.strip() for scope in args.scope.split(",") if scope.strip()}
        result = answer_question(args.question, scopes, settings)
        if args.json:
            # The caller view, not the audit view. See AnswerResult.for_caller.
            print(json.dumps(result.for_caller().model_dump(mode="json"), indent=2))
        else:
            print(format_result(result))
        return 0 if result.status == "answered" else 2

    if args.command == "audit-show":
        # Resolve the record against disk; reprinting it would prove nothing
        # an auditor cannot already read in the answer. The claim worth
        # checking is that each served span still reproduces its quote in the
        # file it names, which is the check the eval gate does internally,
        # exposed to an operator.
        log = AuditLog(settings.audit_log_path, settings.audit_hmac_key)
        if not log.path.exists():
            print(f"NO AUDIT LOG at {log.path}", file=sys.stderr)
            return 1
        records = log.read_all()
        if not 1 <= args.index <= len(records):
            print(f"no record {args.index}: the log holds {len(records)}",
                  file=sys.stderr)
            return 1
        record = records[args.index - 1]
        print(format_record(record, settings))
        return 0 if _record_resolves(record)[0] else 1

    if args.command == "corpus-check":
        return _corpus_check(settings)

    if args.command == "audit-verify":
        log = AuditLog(settings.audit_log_path, settings.audit_hmac_key)
        # verify_chain FIRST. It answers False for an unreadable line as well
        # as for a broken link, so the "BROKEN" message below covers both;
        # read_all() runs only once the file is known to parse.
        # A file that is not there is not a chain that verified. `read_all`
        # treats a missing file as zero records and `verify_chain` is
        # vacuously true over zero records, so without this check a mistyped
        # path would print "audit chain OK: 0 record(s)" and exit 0, the exact
        # sentence a genuinely verified empty log produces.
        if not log.path.exists():
            print(f"NO AUDIT LOG at {log.path}: nothing was verified",
                  file=sys.stderr)
            return 1
        if log.verify_chain():
            records = log.read_all()
            print(f"audit chain OK: {len(records)} record(s) in {log.path}")
            return 0
        position, failing = log.first_failing_record()
        foreign = (
            [position]
            if failing is not None
            and failing.schema_version != RECORD_SCHEMA_VERSION
            else []
        )
        if foreign:
            # A schema boundary is not a break. Say which it is, or the
            # operator goes looking for an intruder who was never there.
            print(
                f"{len(foreign)} record(s) in {log.path} were written under an "
                f"OLDER RECORD SCHEMA and cannot be verified by this build -- "
                f"their hashes cover fields this version does not write. This "
                f"is not evidence of tampering. First affected record: "
                f"{foreign[0]}. Start a new log, or verify them with the "
                f"version that wrote them.",
                file=sys.stderr,
            )
            return 1
        print(f"AUDIT CHAIN BROKEN in {log.path}", file=sys.stderr)
        return 1

    return 1


if __name__ == "__main__":  # pragma: no cover - entry point
    raise SystemExit(main())
