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
from app.evals.runner import gate_failures, held_out_miss_rate, run_evals


def main() -> int:
    settings = get_settings()
    # The gate writes its audit trail to a temporary log so a CI run never
    # appends to whatever is in the working tree's audit/ directory. Nothing
    # under audit/ is committed (.gitignore excludes audit/*.jsonl), so the
    # log this avoids touching is the developer's local one.
    with tempfile.TemporaryDirectory() as directory:
        audit = AuditLog(Path(directory) / "eval.audit.jsonl", settings.audit_hmac_key)
        report = run_evals(settings, audit=audit)
        chain_ok = audit.verify_chain()

    print("Eval metrics")
    for name, value in report.metrics.items():
        print(f"  {name:<24} {value:.3f}")
    print(f"  {'audit_chain_intact':<24} {'yes' if chain_ok else 'NO'}")

    # The held-out set is reported and not gated. A threshold on this number
    # would let the author of the questions also choose the bar they must
    # clear. It is printed so a reader can see what the over-abstention costs
    # on phrasing the golden set does not use, and it is allowed to be bad.
    held_out = held_out_miss_rate(settings)
    print(f"\nHeld-out paraphrases (REPORTED, NOT GATED)")
    print(f"  {'held_out_miss_rate':<24} {held_out:.3f}")
    print(f"  The golden set's false_abstention_rate is "
          f"{report.metrics['false_abstention_rate']:.3f} over questions written")
    print(f"  by the same hand as the retriever. The held-out set asks about "
          f"the same")
    print(f"  corpus in different words.")

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
