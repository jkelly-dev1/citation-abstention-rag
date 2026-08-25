"""Command line entry point.

    python -m app.cli ask "How long are trade confirmations retained?"
    python -m app.cli ask "What was Acme's Q4 revenue?" --scope internal,restricted
    python -m app.cli audit-verify
"""

from __future__ import annotations

import argparse
import json
import sys

from app.audit import AuditLog
from app.config import get_settings
from app.models import AnswerResult
from app.pipeline import DEFAULT_SCOPES, answer_question


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

    args = parser.parse_args(argv)
    settings = get_settings()

    if args.command == "ask":
        scopes = {scope.strip() for scope in args.scope.split(",") if scope.strip()}
        result = answer_question(args.question, scopes, settings)
        if args.json:
            print(json.dumps(result.model_dump(mode="json"), indent=2))
        else:
            print(format_result(result))
        return 0 if result.status == "answered" else 2

    if args.command == "audit-verify":
        log = AuditLog(settings.audit_log_path)
        records = log.read_all()
        if log.verify_chain():
            print(f"audit chain OK: {len(records)} record(s) in {log.path}")
            return 0
        print(f"AUDIT CHAIN BROKEN in {log.path}", file=sys.stderr)
        return 1

    return 1


if __name__ == "__main__":  # pragma: no cover - entry point
    raise SystemExit(main())
