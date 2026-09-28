"""Run the golden set against a provider that is attacking the verifier.

    python3 scripts/adversarial_run.py

"The policy cannot serve an unsupported claim" is the central claim of this
repository, and the shipped mock behaves well except on a handful of scripted
questions, so the ordinary eval run demonstrates the guardrails on those
questions and says nothing about the rest. `HostileProvider` attacks on every
question, five ways at once: a fabricated quote, a chunk id the retriever never
returned, a reversed polarity, a changed figure, and a fabricated second
citation attached to an otherwise real claim.

A pass means every attack claim ended up dropped with the reason code its
attack is meant to trigger, and nothing unsupported was served. Checking only
that served claims are supported cannot fail: the policy serves supported
verdicts and nothing else, so that test passed while a reversed claim, which
the verifier wrongly supported, went out. Settings are pinned to their
defaults, so an environment that loosens a threshold cannot turn the run
green. It does not mean the verifier is complete:
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

# The reason each attack, in the order HostileProvider writes them, must be
# dropped for. The fifth attack (a fabricated second citation on a real claim)
# is not listed: the real citation still supports the claim, and whether it
# is served is the policy's call, not the verifier's.
EXPECTED = ("no_verified_citation", "no_verified_citation",
            "negation_mismatch", "ungrounded_number")
UNRETRIEVED_CHUNK = "acme-fy2025-q4-summary#s01"


def attack_fired(index: int, text: str, retrieved: set[str]) -> bool:
    """Whether attack `index` actually attacked on this case.

    The changed-figure attack rewrites the first number in the quote, so on a
    quote with no number its text IS the quote. The unretrieved-chunk attack
    cites a real sentence, so for a requester cleared to retrieve that chunk
    it is a true claim. Neither is an attack there, and neither is expected
    to be refused.
    """
    if index == 1:
        return UNRETRIEVED_CHUNK not in retrieved
    if index == 3:
        return "999" in text
    return True


class RecordingHostile(HostileProvider):
    """HostileProvider that keeps the claims it sent, so each can be traced."""

    def complete(self, *, question: str, context: str) -> str:
        import json
        raw = super().complete(question=question, context=context)
        self.sent = [claim["text"] for claim in json.loads(raw)["claims"]]
        return raw


def main() -> int:
    import tempfile

    # Every field at its declared default. Keyword arguments outrank the
    # environment and any .env file, so nothing outside this script can
    # loosen what the run checks.
    settings = Settings(**{name: field.default
                           for name, field in Settings.model_fields.items()
                           if not field.is_required()})
    attacks_missed: list[str] = []
    attacks_checked = 0
    served_unsupported: list[str] = []
    refused_reasons: dict[str, int] = {}
    answered = 0

    with tempfile.TemporaryDirectory() as directory:
        audit = AuditLog(Path(directory) / "adversarial.jsonl",
                         settings.audit_hmac_key)
        for case in GOLDEN_SET:
            provider = RecordingHostile()
            result = answer_question(
                case.question, set(case.scopes), settings,
                provider=provider, audit=audit,
            )
            dropped = {claim.text: claim.reasons for claim in result.dropped}
            served = {claim.text for claim in result.claims}
            retrieved = {record.chunk_id for record in result.retrieved}
            for index, (text, reason) in enumerate(
                    zip(getattr(provider, "sent", []), EXPECTED)):
                if not attack_fired(index, text, retrieved):
                    continue
                attacks_checked += 1
                if text in served or reason not in dropped.get(text, []):
                    attacks_missed.append(
                        f"{case.id}: expected {reason}, got "
                        f"{'SERVED' if text in served else dropped.get(text)} "
                        f"-- {text[:60]}")
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
    print(f"  attacks checked          {attacks_checked}")
    print(f"  attacks not refused      {len(attacks_missed)}")
    print(f"  audit_chain_intact       {'yes' if chain_ok else 'NO'}")
    print("  refusals by reason:")
    for reason, count in sorted(refused_reasons.items(), key=lambda kv: -kv[1]):
        print(f"    {reason:<28} {count}")

    if served_unsupported or attacks_missed or not attacks_checked or not chain_ok:
        print("\nADVERSARIAL RUN FAILED")
        for line in served_unsupported:
            print(f"  served an unsupported claim -- {line}")
        for line in attacks_missed:
            print(f"  an attack was not refused for its reason -- {line}")
        if not attacks_checked:
            print("  no attack fired, so nothing was tested")
        if not chain_ok:
            print("  the audit chain did not verify")
        return 1
    print("\nADVERSARIAL RUN PASSED: every attack was refused for its "
          "reason, and nothing unsupported was served")
    return 0


if __name__ == "__main__":
    sys.exit(main())
