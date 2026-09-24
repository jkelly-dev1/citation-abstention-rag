"""Rebuild every figure in README.md from the tree and compare.

    python3 scripts/check_readme_numbers.py

A number typed into prose is a copy, and a copy drifts from its source
without anything failing. Each figure below is derived (collected, counted,
computed or parsed out of the shipped files) and then required to appear in
README.md as an exact string. Where a bare number could match unrelated text,
the string includes the words around it. Nothing here is hand-maintained; if a
figure changes, the derivation changes with it and the prose has to follow.

It prints how many figures it checked, so a version that stopped deriving half
of them is visible instead of clean.

Exit status is 0 when every derived figure appears in the README and 1
otherwise, and that status is what CI reads.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"


def collected_test_count() -> int:
    """Ask pytest for the count; counting `def test_` would undercount.

    Parametrized cases and class-based tests make the grep answer and the real
    answer diverge, and the README quotes the real one.
    """
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--collect-only", "--no-header"],
        cwd=ROOT, capture_output=True, text=True,
    )
    match = re.search(r"(\d+) tests? collected", result.stdout)
    if not match:
        raise SystemExit(
            "could not read a test count from pytest --collect-only; "
            "this checker cannot report on a suite it failed to collect:\n"
            + result.stdout[-2000:] + result.stderr[-2000:]
        )
    return int(match.group(1))


def golden_case_count() -> int:
    sys.path.insert(0, str(ROOT))
    from app.evals.golden import GOLDEN_SET

    return len(GOLDEN_SET)


def claim_row_count() -> int:
    """Rows in the "Claims backed by tests" table."""
    text = README.read_text(encoding="utf-8")
    if "## Claims backed by tests" not in text:
        # Return 0 instead of raising. A checker that dies on an unexpected
        # document reports nothing at all, and "nothing to report" is exactly
        # what a broken checker and a clean one have in common; main() turns
        # this into a stated failure instead.
        return 0
    section = text.split("## Claims backed by tests", 1)[1].split("\n## ", 1)[0]
    rows = [
        line for line in section.splitlines()
        if line.startswith("| ") and not line.startswith("| ---")
        and not line.startswith("| Claim ")
    ]
    return len(rows)


def cited_tests_all_exist() -> list[str]:
    """Every `tests/...::name` the README cites must name a real test.

    A claims table is only worth the rows whose citation resolves. This is the
    check that a renamed test cannot pass.
    """
    text = README.read_text(encoding="utf-8")
    missing = []
    current_file = None
    for path, name in re.findall(r"`(tests/[\w/]+\.py)?::?(test_\w+)`", text):
        if path:
            current_file = path
        target = ROOT / (path or current_file or "")
        if not target.is_file():
            missing.append(f"{path or current_file}::{name} (no such file)")
            continue
        if not re.search(rf"^def {re.escape(name)}\(", target.read_text(encoding="utf-8"), re.M):
            missing.append(f"{target.relative_to(ROOT)}::{name}")
    return missing


def negation_coverage() -> str:
    """The 0.92 in README and in app/verify.py, derived from the corpus.

    The figure belongs to the third sentence of expense-policy#s01 negated.
    Shorter paraphrases of that sentence score 0.86 and 0.88, so the figure is
    computed here by running the real function over the real sentence.
    """
    import re as _re
    sys.path.insert(0, str(ROOT))
    from app.corpus import load_corpus
    from app.verify import claim_coverage

    chunks = {c.chunk_id: c for c in load_corpus(ROOT / "corpus")}
    sentences = _re.split(r"(?<=\.)\s+", chunks["expense-policy#s01"].text.strip())
    quote = next(s for s in sentences if "5,000" in s and "Financial" in s)
    negated = quote.replace("require", "do not require", 1)
    return f"{claim_coverage(negated, [quote]):.2f}"


def python_matrix() -> list[str]:
    """The interpreter versions CI actually runs, out of the workflow file."""
    import re as _re
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    block = _re.search(r"python-version:\s*\[([^\]]+)\]", workflow)
    if not block:
        return []
    return [v.strip().strip("\"'") for v in block.group(1).split(",")]


_WORDS = ("zero", "one", "two", "three", "four", "five", "six", "seven",
          "eight", "nine", "ten", "eleven", "twelve")


def fabrication_case_count() -> str:
    """Golden cases tagged as scripting a fabrication, as the README words it.

    The README spells the count out ("five cases script a fabrication"), and a
    bare digit would be found somewhere in any README, so the needle is the
    phrase.
    """
    sys.path.insert(0, str(ROOT))
    from app.evals.golden import GOLDEN_SET

    count = sum("fabrication" in case.tags for case in GOLDEN_SET)
    return f"{_WORDS[count]} cases script a fabrication"


def held_out_figures() -> dict[str, str]:
    """The held-out set's size, misses and miss rate, computed by running it."""
    sys.path.insert(0, str(ROOT))
    from app.evals.paraphrases import HELD_OUT
    from app.evals.runner import held_out_miss_rate

    rate = held_out_miss_rate()
    return {
        "held-out questions": f"holds {len(HELD_OUT)} answerable questions",
        "held-out misses": f"misses {round(rate * len(HELD_OUT))} of them",
        "held-out miss rate": f"{rate:.3f}",
    }


def undocumented_figures(text: str, derived: dict) -> list[str]:
    """Numbers in README prose that nothing here derives.

    The README does not claim every figure is derived. It says this script
    prints the ones it does not derive, and this is where that list comes
    from.

    Code fences, test node ids, section anchors and version pins are skipped:
    they are not figures that can drift from a source, and counting them would
    bury the ones that can.
    """
    import re as _re
    # Mask, do not delete. Removing the fenced blocks would shift every offset
    # after them, and the "prose line ~N" printed below would point at an
    # unrelated line. Replacing each masked run with blanks of the same length
    # keeps every remaining character at its original offset.
    blank = lambda m: _re.sub(r"[^\n]", " ", m.group(0))
    body = _re.sub(r"```.*?```", blank, text, flags=_re.S)
    body = _re.sub(r"`[^`]*`", blank, body)
    covered = " ".join(str(v) for v in derived.values())
    out = []
    # Comma-grouped numbers are one figure, so "5,000" is one token and not
    # "5" and "000". A hyphen does not join two numbers into one: the
    # "500-to-5,000" band is two figures. A number hyphenated onto a word, as
    # in SHA-256, is part of a name and not a figure.
    for m in _re.finditer(r"(?<![\w.,])(?<![A-Za-z]-)\d(?:\d|,\d{3})*(?:\.\d+)?(?![\w.]|,\d)", body):
        token = m.group(0)
        if token in covered:
            continue
        line = body[:m.start()].count("\n") + 1
        out.append(f"{token} (prose line ~{line})")
    return out


def main() -> int:
    text = README.read_text(encoding="utf-8")
    figures = {
        "collected tests": f"{collected_test_count()} tests",
        "golden set cases": f"{golden_case_count()} cases",
        "negation coverage": negation_coverage(),
        "fabrication cases": fabrication_case_count(),
        **held_out_figures(),
    }
    for version in python_matrix():
        figures[f"CI python {version}"] = version

    # A phrase can wrap across lines in the source, so compare with runs of
    # whitespace collapsed to one space.
    flat = " ".join(text.split())
    failures = []
    for label, needle in figures.items():
        if needle not in flat:
            failures.append(f"{label}: README does not contain {needle!r}")

    rows = claim_row_count()
    if rows < 1:
        failures.append("claims table: no rows found, so nothing was checked")

    missing = cited_tests_all_exist()
    failures.extend(f"claims table cites a test that does not exist: {m}"
                    for m in missing)

    uncovered = undocumented_figures(text, figures)
    print(f"checked {len(figures)} derived figure(s) and "
          f"{rows} claims-table row(s) against README.md")
    for label, needle in figures.items():
        print(f"  {label:<20} {needle}")
    # Say what is not covered. A checker that reports only its successes lets
    # "every figure is checked" survive on the strength of the figures it
    # happens to check.
    print(f"  {'NOT derived here':<20} {len(uncovered)} number(s) in prose"
          + (": " + ", ".join(uncovered[:8]) if uncovered else "")
          + (", ..." if len(uncovered) > 8 else ""))

    if failures:
        print("\nREADME NUMBERS FAILED")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("\nREADME NUMBERS OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
