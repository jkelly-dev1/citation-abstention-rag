"""End to end demo.

Runs a fixed set of questions through the real pipeline, prints what a caller
would see, then verifies the audit chain and shows what tampering with it looks
like. Output is captured verbatim in SAMPLE_RUN.md.

    python scripts/run_demo.py
    ENV_FILE=~/.secrets/ai.env AGENT_PROVIDER=anthropic python scripts/run_demo.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.audit import AuditLog  # noqa: E402
from app.cli import format_result  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.evals.runner import gate_failures, run_evals  # noqa: E402
from app.llm import get_provider  # noqa: E402
from app.pipeline import answer_question  # noqa: E402

DEMO_LOG = "audit/demo.audit.jsonl"

CASES: list[tuple[str, str, set[str]]] = [
    (
        "A question the corpus answers",
        "Who approves an expense above 5,000 USD?",
        {"public", "internal"},
    ),
    (
        "A question with no answer anywhere in the corpus",
        "How many people work in the Zurich office?",
        {"public", "internal"},
    ),
    (
        "The answer exists, but in a document this requester is not cleared for",
        "What revenue did Acme Holdings report for the fourth quarter?",
        {"public", "internal"},
    ),
    (
        "The same question, from a requester who is cleared for it",
        "What revenue did Acme Holdings report for the fourth quarter?",
        {"public", "internal", "restricted"},
    ),
    (
        "The model quotes text that exists nowhere in the corpus",
        "What is the CEO's travel budget under the expense policy?",
        {"public", "internal"},
    ),
    (
        "The model quotes a real sentence but changes the number in it",
        "How many years are trade confirmations kept for under the standard?",
        {"public", "internal"},
    ),
    (
        "The model cites a real chunk it was never shown",
        "Who approved the Acme Q4 figure in the expense system?",
        {"public", "internal"},
    ),
    (
        "One grounded claim and one invented claim in the same answer",
        "Summarize the expense approval thresholds and the CEO bonus.",
        {"public", "internal"},
    ),
]


def main() -> int:
    settings = get_settings()
    provider = get_provider(settings)
    log_path = Path(DEMO_LOG)
    if log_path.exists():
        log_path.unlink()
    audit = AuditLog(log_path, settings.audit_hmac_key)

    print("=" * 78)
    print("citation-abstention-rag demo")
    print(f"provider: {provider.name} ({provider.model})")
    if provider.name == "mock":
        print(
            "note: cases 5 to 8 are scripted misbehavior in the mock, so each\n"
            "      guardrail fires deterministically. Against a real model the\n"
            "      same questions show what that model actually does."
        )
    else:
        print(
            "note: cases 5 to 8 are the questions that make the mock misbehave.\n"
            "      A real model may answer them correctly or decline; both are\n"
            "      reported here as they happened."
        )
    print("=" * 78)

    for index, (label, question, scopes) in enumerate(CASES, start=1):
        print(f"\n--- {index}. {label} " + "-" * max(0, 60 - len(label)))
        # A fixed request id and a fixed clock, because this output is
        # committed to SAMPLE_RUN.md and compared byte for byte. A random uuid
        # and a wall clock reading make the capture impossible to reproduce,
        # and a capture that cannot be reproduced is not evidence of anything.
        # The values are obviously synthetic so nobody mistakes them for a
        # real run's.
        result = answer_question(
            question, scopes, settings, provider, audit,
            request_id=f"demo{index:028d}",
            now="2026-01-01T00:00:00+00:00",
        )
        print(format_result(result))

    print("\n" + "=" * 78)
    print("Audit trail")
    print("=" * 78)
    records = audit.read_all()
    print(f"records written : {len(records)}")
    print(f"chain verifies  : {audit.verify_chain()}")
    first = records[0]
    print("\nfirst record (truncated):")
    payload = first.model_dump(mode="json")
    payload["served_claims"] = [claim["text"] for claim in payload["served_claims"]]
    payload["dropped_claims"] = [claim["text"] for claim in payload["dropped_claims"]]
    print(json.dumps(payload, indent=2)[:1200])

    # Show that editing a past decision is detectable.
    lines = log_path.read_text(encoding="utf-8").splitlines()
    tampered = json.loads(lines[1])
    tampered["status"] = "answered"
    lines[1] = json.dumps(tampered)
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nafter editing record 2 in place, chain verifies: {audit.verify_chain()}")

    print("\n" + "=" * 78)
    print("Eval gate (always runs on the deterministic mock provider)")
    print("=" * 78)
    report = run_evals(settings, audit=AuditLog(Path(DEMO_LOG).with_suffix(".eval.jsonl")))
    for name, value in report.metrics.items():
        print(f"  {name:<24} {value:.3f}")
    failures = gate_failures(report, settings)
    print(f"\ngate: {'PASS' if not failures else 'FAIL'}")
    for failure in failures:
        print(f"  - {failure}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
