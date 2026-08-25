"""CI gate: exits 1 when the golden set regresses.

This is the guardrail that makes the rest of the repo more than a demo. A
prompt change that makes the system fabricate, a threshold change that makes it
answer questions it cannot ground, or a retrieval change that leaks a
restricted document all fail the build here.

It always runs against the deterministic mock provider, because a regression
gate has to be reproducible: the golden set pins which guardrail fires for each
case, and a live model moves those around between runs. Real model behavior
belongs in SAMPLE_RUN.md, not in a pass or fail signal for CI.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from app.audit import AuditLog
from app.config import get_settings
from app.evals.runner import gate_failures, run_evals


def main() -> int:
    settings = get_settings()
    # The gate writes its audit trail to a temporary log so a CI run never
    # appends to the committed sample log.
    with tempfile.TemporaryDirectory() as directory:
        audit = AuditLog(Path(directory) / "eval.audit.jsonl")
        report = run_evals(settings, audit=audit)
        chain_ok = audit.verify_chain()

    print("Eval metrics")
    for name, value in report.metrics.items():
        print(f"  {name:<24} {value:.3f}")
    print(f"  {'audit_chain_intact':<24} {'yes' if chain_ok else 'NO'}")

    failures = gate_failures(report, settings)
    if not chain_ok:
        failures.append("audit chain verification failed")

    if failures:
        print("\nEVAL GATE FAILED")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    print(f"\nEVAL GATE PASSED ({int(report.metrics['cases'])} cases)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
