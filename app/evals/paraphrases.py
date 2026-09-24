"""A held-out set: the same corpus, questions worded differently.

It is separate from the golden set because `false_abstention_rate 0.000` over
seven questions written by the person who wrote the retriever is not evidence
that the system answers real questions; it is evidence that it answers those
seven. A perfect score on an author-written set should be read as a warning.

It is reported and not gated. These questions have the same author as the
golden set, and a threshold chosen by that author would prove nothing. The
eval gate prints the miss rate, `scripts/check_readme_numbers.py` derives it
into the README, and it is allowed to be bad.

Every question here is answerable from `corpus/` by a cooperative reader, and
none reuses a golden-set question's wording. A question the corpus cannot
answer would be a correct abstention and would inflate the miss rate, making
the system look worse than it is, which is as misleading as making it look
better. `expect_doc` is the document a correct answer has to come from, so an
answer drawn from the wrong place counts as a miss.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Paraphrase:
    question: str
    expect_doc: str


HELD_OUT: tuple[Paraphrase, ...] = (
    Paraphrase("Can I expense alcohol?", "expense-policy"),
    Paraphrase("Is a taxi to the airport reimbursable?", "expense-policy"),
    Paraphrase("Do I need sign-off to spend four thousand dollars?", "expense-policy"),
    Paraphrase("Who signs off on a model before it goes live?", "model-risk-policy"),
    Paraphrase("How often are models reviewed after launch?", "model-risk-policy"),
    Paraphrase("Can a model go live before anyone independent has checked it?",
               "model-risk-policy"),
    Paraphrase("How quickly must we tell legal about a breach?",
               "incident-response-runbook"),
    Paraphrase("Who has to be told about a severity one, and how fast?",
               "incident-response-runbook"),
    Paraphrase("How soon after an incident closes must the write-up be out?",
               "incident-response-runbook"),
    Paraphrase("How long do we hang on to routine business letters?",
               "data-retention-standard"),
    Paraphrase("How long is ordinary business correspondence kept?",
               "data-retention-standard"),
    Paraphrase("When do we destroy candidate files?", "data-retention-standard"),
)
