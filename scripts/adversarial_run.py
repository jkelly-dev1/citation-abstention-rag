"""Run the golden set against a provider that is attacking the verifier.

    python3 scripts/adversarial_run.py

"The policy cannot serve an unsupported claim" is the central claim of this
repository, and the shipped mock behaves well except on a handful of scripted
questions, so the ordinary eval run demonstrates the guardrails on those
questions and says nothing about the rest. `HostileProvider` attacks on every
question, five ways at once: a fabricated quote, a chunk id the retriever never
returned, a reversed polarity, a changed figure, and a fabricated second
citation attached to an otherwise real claim.

A pass means `unsupported_served` stayed at zero while something tried on
every case to push a claim through. It does not mean the verifier is complete:
this adversary uses the five attacks the verifier is known to check for, so it
measures that the known controls hold, not that there is no sixth attack. The README's honest limits still apply.

Exit status is 0 when nothing unsupported was served and 1 otherwise, so this
can be wired into CI beside the gate.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.audit import AuditLog  # noqa: E402
from app.config import Settings  # noqa: E402
from app.evals.golden import GOLDEN_SET  # noqa: E402
from app.llm import HostileProvider  # noqa: E402
from app.pipeline import answer_question  # noqa: E402


def main() -> int:
    import tempfile

    settings = Settings()
    served_unsupported: list[str] = []
    refused_reasons: dict[str, int] = {}
    answered = 0

    with tempfile.TemporaryDirectory() as directory:
        audit = AuditLog(Path(directory) / "adversarial.jsonl",
                         settings.audit_hmac_key)
        for case in GOLDEN_SET:
            result = answer_question(
                case.question, set(case.scopes), settings,
                provider=HostileProvider(), audit=audit,
            )
            if result.status == "answered":
                answered += 1
            for claim in result.claims:
                # The assertion is about what was served, not about what was
                # refused. A claim in `claims` that is not `supported` is the
                # failure this whole repository is built to prevent.
                if not claim.supported:
                    served_unsupported.append(f"{case.id}: {claim.text[:70]}")
            for claim in result.dropped:
                for reason in claim.reasons or ["(verified, not served)"]:
                    refused_reasons[reason] = refused_reasons.get(reason, 0) + 1
        chain_ok = audit.verify_chain()

    print(f"adversarial run over {len(GOLDEN_SET)} golden case(s), "
          f"provider=hostile")
    print(f"  answered at all          {answered}")
    print(f"  unsupported_served       {len(served_unsupported)}")
    print(f"  audit_chain_intact       {'yes' if chain_ok else 'NO'}")
    print("  refusals by reason:")
    for reason, count in sorted(refused_reasons.items(), key=lambda kv: -kv[1]):
        print(f"    {reason:<28} {count}")

    if served_unsupported or not chain_ok:
        print("\nADVERSARIAL RUN FAILED")
        for line in served_unsupported:
            print(f"  served an unsupported claim -- {line}")
        if not chain_ok:
            print("  the audit chain did not verify")
        return 1
    print("\nADVERSARIAL RUN PASSED: nothing unsupported was served")
    return 0


if __name__ == "__main__":
    sys.exit(main())
