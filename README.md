# citation-abstention-rag

[![CI](https://github.com/jkelly-dev1/citation-abstention-rag/actions/workflows/ci.yml/badge.svg)](https://github.com/jkelly-dev1/citation-abstention-rag/actions/workflows/ci.yml)

A retrieval augmented question answering system, built as a personal learning
project, that treats an unsupported answer as a defect rather than a rough
edge. Every claim it serves carries a citation the system itself verified back
to a character span in a source document. When it cannot ground an answer, it
abstains and says why. Every request, answered or refused, lands in a hash
chained audit log.

It runs fully offline on a deterministic mock provider and switches to a real
model (Anthropic or OpenAI) with two environment variables. It has been run
end to end against `claude-opus-5` and `gpt-4o`; both captures are in
`SAMPLE_RUN.md`, along with what those runs show that the mock cannot.

## The problem it addresses

Accuracy is not what makes a retrieval system usable on regulated data. The
questions that stop a deployment are these: can this answer be traced back to a
source, what happens when the model is confidently wrong, and who can show
afterwards what the system saw and decided. Waiting for a more accurate model
does not answer any of them. Architecture does.

Four controls, all implemented here:

- **Per claim attribution.** A claim is served only if it cites a quote that
  resolves to an exact span of a source document, and the claim's own
  vocabulary, figures, and polarity match that quote. What is checked is
  overlap, not entailment; the limits below say exactly where that stops.
- **Abstention with reason codes.** Below threshold, the system refuses rather
  than guessing, and the refusal names the reason.
- Scoped retrieval. Documents outside the requester's clearance are filtered
  before scoring, so they are never ranked, never shown to the model, and never
  citable.
- **A hash chained audit log.** Every decision is recorded, including the
  claims that were withheld and why. How much tampering that detects depends
  on whether `AUDIT_HMAC_KEY` is set; see the limits below.

## What it demonstrates

- Citations are checked, not trusted. The model's quote must appear verbatim in
  the chunk it cites, and that chunk must be one retrieval actually returned.
- Numbers get their own check. A claim that quotes a real sentence but changes
  a figure inside it is the failure mode that matters most on financial text,
  and lexical overlap alone accepts it.
- The abstention policy is explicit and tunable: retrieval confidence, claim
  support ratio, and answer relevance each have a named threshold and a reason
  code.
- Scoped retrieval is enforced at two layers, so a model that names a
  restricted chunk id it was never shown is refused at verification.
- An eval gate fails CI when the golden set regresses, including when a
  threshold change makes the system answer things it should refuse. Its exit
  code is itself under test, because a gate whose exit code nothing reads is a
  report, not a gate.
- No vector database and no embedding service: BM25 and a light stemmer in pure
  Python. The gate is what this project is about, not the index.

## Claims backed by tests

| Claim | Test |
| --- | --- |
| A verified citation resolves to the exact span of the source document | `tests/test_verify.py::test_verified_citation_offsets_point_at_the_quote_in_the_source` |
| Chunk offsets reproduce the source text exactly | `tests/test_corpus.py::test_chunk_offsets_reproduce_the_chunk_text_exactly` |
| A quote that exists nowhere in the source is refused | `tests/test_verify.py::test_fabricated_quote_is_rejected` |
| A citation to a chunk retrieval did not return is refused | `tests/test_verify.py::test_citation_to_a_chunk_that_was_not_retrieved_is_rejected` |
| A real quote with a changed number is unsupported | `tests/test_verify.py::test_claim_quoting_a_real_sentence_but_stating_another_number_is_unsupported` (mutation-checked: disable numeric grounding and it fails) |
| A claim whose content is absent from its quote is unsupported | `tests/test_verify.py::test_claim_whose_content_is_absent_from_its_quote_is_unsupported` |
| A claim that negates its own quote is unsupported, and a faithful one is not | `tests/test_verify.py::test_a_claim_that_negates_its_own_quote_is_unsupported`, `::test_a_faithful_claim_is_not_punished_by_the_negation_check` (mutation-checked: drop the polarity branch and the first fails; make it always fire and the second does) |
| An empty or whitespace quote is refused, not resolved to the start of the chunk | `tests/test_verify.py::test_an_empty_or_whitespace_quote_is_refused` (mutation-checked: delete the guard in `locate_quote` and it fails) |
| Quote matching tolerates whitespace, case, and curly quotes | `tests/test_verify.py::test_quote_matching_tolerates_whitespace_case_and_curly_quotes` |
| Out of scope documents are never retrieved | `tests/test_retrieval.py::test_out_of_scope_chunks_are_never_returned` |
| The scope filter runs before scoring, not after | `tests/test_retrieval.py::test_scope_filter_runs_before_scoring` (mutation-checked: move the filter after scoring and it fails) |
| The same question answers only when the requester is cleared | `tests/test_pipeline.py::test_restricted_answer_is_withheld_without_clearance` |
| A requester cleared for nothing is not silently given the default clearance | `tests/test_pipeline.py::test_an_empty_clearance_set_is_not_the_default_clearance` (mutation-checked: restore the truthiness coercion and it fails) |
| A corpus document with no scope label is withheld, not served | `tests/test_corpus.py::test_a_document_with_no_scope_label_is_withheld_rather_than_served` (mutation-checked: default the label to `internal` and it fails) |
| A model citing a withheld chunk gets nothing served | `tests/test_pipeline.py::test_a_citation_to_a_withheld_chunk_is_refused` |
| Weak retrieval abstains before the model is called at all | `tests/test_pipeline.py::test_unanswerable_question_abstains_without_calling_the_model` |
| A partly fabricated answer is refused whole, not trimmed and served | `tests/test_policy.py::test_too_few_supported_claims_abstains_rather_than_serving_a_fragment` (mutation-checked: set the ratio to 0 and it fails) |
| A grounded but off topic answer is refused | `tests/test_policy.py::test_grounded_but_off_topic_answer_is_refused` (mutation-checked: set relevance to 0 and it fails) |
| Relevance is scored over the served answer, not over the quotes it cites | `tests/test_policy.py::test_relevance_is_scored_over_the_answer_not_over_its_quotes` (mutation-checked: put the quotes back in the denominator and it fails) |
| Unsupported claims never reach the caller | `tests/test_pipeline.py::test_unsupported_claims_never_reach_the_caller` |
| Every request writes exactly one audit record | `tests/test_pipeline.py::test_every_request_writes_one_audit_record_and_the_chain_verifies` |
| Editing, reordering, or deleting an audit record is detected | `tests/test_audit.py::test_editing_a_past_record_breaks_the_chain`, `::test_reordering_records_breaks_the_chain`, `::test_deleting_a_middle_record_breaks_the_chain` |
| An editor who recomputes the whole chain is caught only when a key is set | `tests/test_audit.py::test_a_keyed_chain_refuses_an_editor_who_lacks_the_key`, `::test_an_unkeyed_chain_accepts_an_editor_who_recomputes_it` (mutation-checked: make the digest ignore the key and the first fails) |
| A corrupt log line reports a broken chain instead of a traceback | `tests/test_audit.py::test_a_corrupt_line_is_reported_as_a_broken_chain_not_a_traceback`, `::test_the_cli_reports_a_corrupt_log_instead_of_crashing` |
| The chain link itself is covered by each record's hash | `tests/test_audit.py::test_prev_hash_is_covered_by_the_record_hash` (mutation-checked: drop `prev_hash` from the hashed payload and it fails) |
| Each audit record says when it was decided and under which thresholds | `tests/test_audit.py::test_the_audit_record_says_when_under_what_rules_and_from_what_corpus`, `::test_the_settings_digest_moves_with_a_threshold_and_not_with_a_path` (mutation-checked: drop the `min_` prefix from the digest's selection and it fails) |
| Provider selection needs both the name and the credential; keys never cross match | `tests/test_llm.py::test_provider_name_without_a_key_falls_back_to_mock`, `::test_keys_do_not_cross_match_between_providers` |
| Fenced or prose wrapped model output still parses | `tests/test_llm.py::test_parser_strips_code_fences_and_surrounding_prose` |
| Unparseable model output abstains instead of raising | `tests/test_llm.py::test_unparseable_output_abstains_instead_of_raising` |
| Every unreadable model output shape abstains rather than raising, including a TRUNCATED reply | `tests/test_llm.py::test_every_unreadable_shape_abstains_rather_than_raising` (mutation-checked: make the JSONDecodeError branch raise and it fails) |
| An unreadable reply and an honest decline get different reason codes | `tests/test_llm.py::test_an_unreadable_reply_is_not_recorded_as_the_model_declining`, `tests/test_policy.py::test_an_unreadable_reply_gets_its_own_reason_code` (mutation-checked: route a parse failure back to `model_declined` and both fail) |
| The eval gate catches a real regression, not just its own thresholds | `tests/test_evals.py::test_gate_fails_when_numeric_grounding_is_disabled`, `::test_gate_fails_when_the_scope_filter_is_widened`, `::test_gate_fails_when_the_abstention_thresholds_are_removed` |
| The gate's exit code, not just its output, says pass or fail | `tests/test_evals.py::test_the_gate_exit_code_says_pass_or_fail` (mutation-checked: change `return 1` to `return 0` and it fails) |
| Each of the five metric thresholds reports its own breach | `tests/test_evals.py::test_every_threshold_branch_reports_its_own_breach` (mutation-checked: delete any one branch and it fails) |
| A served citation whose recorded span drifts off its quote is counted | `tests/test_evals.py::test_bad_offsets_counts_a_span_that_does_not_reproduce_its_quote` (mutation-checked: `return 0` from `_check_offsets` and it fails) |
| The golden set's expected reason code is enforced, not decoration | `tests/test_evals.py::test_a_case_expecting_the_wrong_reason_code_fails_the_gate` (mutation-checked: delete the comparison and it fails) |
| The figures in this README are derived from the tree, and a wrong one fails the check | `tests/test_evals.py::test_the_readme_number_checker_agrees_with_the_tree`, `::test_the_readme_number_checker_refuses_a_wrong_count_in_words`, `::test_the_readme_number_checker_derives_the_held_out_miss_rate` (mutation-checked: compare the fabrication count as a bare digit and it fails) |
| Singular and plural forms of a word retrieve each other, which a real model's wording depends on | `tests/test_retrieval.py::test_stemmer_unifies_common_inflections` |
| A stemmer collision stays below the abstention threshold | `tests/test_retrieval.py::test_a_stemmer_collision_still_lands_below_the_abstention_threshold` |

## Quickstart

Requires Python 3.11 or newer. CI runs 3.11, 3.12, and 3.13.

```
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt

pytest -q                     # 118 tests, fully offline
python scripts/run_demo.py    # end to end demo over the whole corpus
python -m app.evals.gate      # the CI eval gate, 16 cases
```

Ask it something:

```
python -m app.cli ask "Who approves an expense above 5,000 USD?"
python -m app.cli ask "How many people work in the Zurich office?"
python -m app.cli ask "What revenue did Acme Holdings report for the fourth quarter?"
python -m app.cli ask "What revenue did Acme Holdings report for the fourth quarter?" \
  --scope internal,restricted
python -m app.cli audit-verify
python -m app.cli audit-show 1
python -m app.cli corpus-check
```

Exit codes matter here because a scripted caller reads them and not the
prose.

| Command | 0 | 1 | 2 |
| --- | --- | --- | --- |
| `ask` | answered | -- | abstained |
| `ask` (unreadable audit log) | -- | log named and refused | -- |
| `audit-verify` | chain verified | broken, unreadable, or no log at that path | -- |
| `audit-show N` | every served span still reproduces its quote | a span does not resolve, or no record N | -- |
| `corpus-check` | every document is readable by somebody | a document has no `scope:` and answers nobody | -- |
| any | -- | -- | argparse rejected the arguments |

An abstention is a successful outcome of a question and a failure of nothing,
so `ask` distinguishes it from an error with 2 rather than 1. `audit-show`
re-opens each cited span on disk and compares it against the quote the record
stored, so a nonzero exit there means the corpus moved under the record.

`SAMPLE_RUN.md` holds a verbatim capture of the demo output.
`scripts/check_readme_numbers.py`, which CI runs, re-derives the figures in
this file from the tree (the test count, the golden-set size, the negation
coverage value, the fabrication-case count, the held-out figures and the CI
interpreter matrix) and requires each to appear here as an exact string. It
also checks that every test this file cites by name exists. It prints the
figures it does not derive, with their line numbers, so the gap is visible.
Those are the two `1.000` invariants, which the Honest limits section explains
are constant by construction, and example values quoted from `corpus/` (the
approval thresholds, the revenue figure, the retention period) to illustrate a
check.

## How a request flows

```
question + requester scopes
        |
        v
  scoped retrieval        scope filter runs first: out of scope chunks are
        |                 never scored, ranked, shown, or citable
        v
  confidence < threshold? --> abstain (no_relevant_source), model never called
        |
        v
  model produces claims + citations   (mock / Anthropic / OpenAI)
        |
        v
  verification            chunk must have been retrieved; quote must appear
        |                 verbatim; content and numbers must be covered
        v
  abstention policy       supported ratio, answer relevance
        |
        v
  answer with spans, or abstention with reason codes
        |
        v
  hash chained audit record (served claims, withheld claims, retrieval, model)
```

## Reason codes

| Code | Meaning |
| --- | --- |
| `no_relevant_source` | Retrieval confidence below threshold. The model is not called. |
| `model_declined` | The model itself said the context does not answer the question. |
| `model_output_unparseable` | The reply could not be read at all: truncated by a length cap, empty, or prose where JSON was required. |
| `model_produced_no_claims` | The reply parsed, and contained no claims. |
| `no_supported_claims` | Every claim failed verification. |
| `insufficient_support` | Some claims survived, but too few to serve the answer. |
| `answer_not_relevant` | The surviving claims are grounded but do not address the question. |

Per citation failures are recorded separately: `chunk_not_retrieved`,
`quote_not_found_in_source`, `claim_not_covered_by_quote`, `ungrounded_number`,
`negation_mismatch`, `no_citation`, `no_verified_citation`.

## Real models

The system switches from the offline mock when `AGENT_PROVIDER` is set to
`anthropic` or `openai` and the matching API key is present:

```
ENV_FILE=~/.secrets/ai.env AGENT_PROVIDER=anthropic python scripts/run_demo.py
ENV_FILE=~/.secrets/ai.env AGENT_PROVIDER=openai    python scripts/run_demo.py
```

Selection requires both the provider name and its credential; anything else
falls back to the mock, so tests and CI never touch the network. Both SDKs are
imported lazily and are not loaded at all in mock mode. Credentials come from
the environment, a gitignored `.env`, or an `ENV_FILE` pointing at a private
file outside the repository.

The Anthropic path asks for schema constrained JSON and falls back to
instruction only if the installed SDK does not accept it. Either way the
response goes through a tolerant parser, because a real model sometimes wraps
JSON in fences or prose no matter what the system prompt says.

## Design notes and honest limits

- **The relevance and coverage checks measure vocabulary overlap, not
  entailment.** They catch a claim whose content is simply not in the quote it
  cites. They are blind by construction to a claim that reuses the quote's
  words and changes what they say, because a fraction of matched tokens cannot
  weigh the one token that carries the meaning.

  Two such rewrites have their own checks: a changed figure
  (`ungrounded_number`) and a flipped polarity (`negation_mismatch`, which
  compares negation words as a set). Negate the expense sentence ("Expenses
  above 5,000 USD require written approval from the Chief Financial Officer
  before the expense is incurred") and the contradiction scores 0.92 against
  the sentence it contradicts, because the added "not" is the only one of the
  claim's 13 content words the quote does not contain. That figure is derived
  by `scripts/check_readme_numbers.py`, not typed here.

  Anything subtler than those two still passes: a swapped actor, a changed
  condition, a reversed direction. A natural language inference model or an LLM
  judge drops into `verify.claim_coverage` and nothing else in the pipeline
  changes.
- **The polarity check over-abstains.** A claim that legitimately paraphrases
  "no employee may X unless Y" into "employees may X only after Y" is refused,
  because the negation words do not match. That is the direction this project
  fails in deliberately.
- **Answer relevance is scored over the served claims alone.** The verified
  quotes are left out because they come from the chunk retrieval already
  matched on the question's words, so scoring them would largely re-measure
  retrieval and let an answer ride in on the passage it cites.
- **The audit chain is only as strong as its key.** Unkeyed, which is the
  default, the digest is a plain SHA-256, so the chain catches an edit made in
  place but not an editor who changes a record and recomputes the whole chain
  from there.
  That is integrity against corruption and accident, not against an adversary.
  Set `AUDIT_HMAC_KEY` and re-chaining requires the key; the key then has to
  live somewhere the log writer cannot be rewritten from, which is a deployment
  question this repository does not answer. Both halves are pinned by tests,
  including the weakness.
- Retrieval confidence is a heuristic, not a probability. It is the top BM25
  score divided by a saturation constant and clipped to 1.0. It is used as an
  abstention trigger and is calibrated against the golden set, not against a
  held out distribution.
- **BM25 with a crude suffix stemmer will miss paraphrases.** That failure mode
  shows up as over-abstention, which is the direction to fail in. Swapping in
  embeddings changes `retrieval.py` only.
- **The corpus is synthetic.** The documents are written to look like the
  regulated material this design is for, and no real company data is in this
  repository.
- **Provenance is not truth.** Verification proves a served claim came from
  `corpus/`; nothing checks that `corpus/` is right. A document dropped in
  there is retrieved, quoted and served like any other. `corpus/` is the trust
  boundary. Records carry a per-document SHA-256 and `audit-show` re-resolves
  a citation against disk, so a change is detectable afterwards, which is not
  the same as prevented.
- **A claim must be carried by ONE sentence of its quote.** Coverage and
  numeric grounding are scored against the single best sentence, not the union
  of everything quoted, because a model that quotes a whole section otherwise
  makes every claim about that section pass. The cost is that a claim which
  faithfully synthesizes two adjacent sentences is refused.
- **Off-question padding is only partly caught.** A served claim that shares no
  content word with the question is dropped, but a claim sharing the question's
  vocabulary while answering a different part of it is served: "who approves
  above 5,000" comes back with the 500-to-5,000 band too. Telling those apart
  needs entailment.
- The stemmer is a one-pass suffix stripper and does not collapse every word
  family. "approves/approved/approval" share a stem; "notify/notifies/
  notification" do not. A question phrased around one member of a split family
  scores lower against a passage phrased around another. Swapping in a real
  analyzer is a drop-in at `app/retrieval.py`'s `stem`.
- Numeric grounding is strict about the QUANTITY, not just the digits: a
  changed magnitude, unit or currency ("412.6 million" -> "412.6 billion",
  "7 years" -> "7 days", "5,000 USD" -> "5,000 EUR") is refused, and a
  spelled-out number is compared against its digits. What it still cannot see
  is a quantity that is absent from the claim altogether.
- Numeric grounding is strict. A claim containing a number not present in its
  quotes is refused even if the rest is fine.
- **`citation_precision` is an invariant.** It is measured over served
  citations, and the policy cannot serve a claim that has none verified, so
  with the shipped mock (one citation per claim) it reads 1.000 by
  construction, and `EVAL_MIN_CITATION_PRECISION` asserts that the invariant
  still holds instead of discriminating between runs. It moves only when a
  model offers several citations for one claim and some fail.
  `model_citation_precision` is the one that reports on the model: it counts
  every citation produced, including those on claims that were dropped, and on
  the golden set it reads well below 1.000 because five cases script a
  fabrication.
- **Ordinary phrasing over-abstains, and that is measured, not gated.**
  `app/evals/paraphrases.py` holds 12 answerable questions worded differently
  from the golden set, and the pipeline misses 8 of them: `held_out_miss_rate`
  reads 0.667. A miss is an abstention, or an answer cited from the wrong
  document. The eval gate prints the figure and does not fail on it, because
  the questions were written by the same author as the golden set and a bar
  chosen by that author would prove nothing.
- **Multi-part questions over-abstain.** If a question asks two things and the
  corpus answers one, the unanswerable half's words stay in the relevance
  denominator and can pull the whole answer below threshold. Both real models
  hit this on the same question, answering the half they could and saying so
  about the other half, and the system still refused. Scoring relevance against
  the answerable portion of a question is the fix; it is not implemented.
- **The demo prints withheld claims so you can see why.** A production caller
  would get reason codes only; the withheld text stays in the audit log.
- The eval gate always runs on the mock. It is a regression gate for the
  pipeline, not a benchmark for the model of the day. Running it against a live
  model produced five reason-code failures on cases where the system had behaved
  correctly, because the model declined where the mock was scripted to
  fabricate. Real model behavior belongs in `SAMPLE_RUN.md`.

## Scope

This is a small learning project. There is no HTTP API, no
authentication, no user management, no vector store, and one worked corpus.
The scope labels are a stand in for a real entitlement system. Those are seams,
not oversights. The design mirrors the discipline of my other portfolio repos
([prompt-injection-benchmark](https://github.com/jkelly-dev1/prompt-injection-benchmark),
[ai-data-boundary-proxy](https://github.com/jkelly-dev1/ai-data-boundary-proxy),
[llm-eval-gate](https://github.com/jkelly-dev1/llm-eval-gate),
[least-privilege-agent](https://github.com/jkelly-dev1/least-privilege-agent),
[temporal-multi-agent](https://github.com/jkelly-dev1/temporal-multi-agent),
[agentic-review-gate](https://github.com/jkelly-dev1/agentic-review-gate),
[typed-agent-service](https://github.com/jkelly-dev1/typed-agent-service),
[federated-retrieval-router](https://github.com/jkelly-dev1/federated-retrieval-router),
[hardened-mcp-server](https://github.com/jkelly-dev1/hardened-mcp-server),
[vlm-extraction-integrity](https://github.com/jkelly-dev1/vlm-extraction-integrity),
[llm-observability-stack](https://github.com/jkelly-dev1/llm-observability-stack),
[ai-compliance-checker](https://github.com/jkelly-dev1/ai-compliance-checker),
[airgapped-ai-bundle](https://github.com/jkelly-dev1/airgapped-ai-bundle),
[agent-sandbox-escape](https://github.com/jkelly-dev1/agent-sandbox-escape),
[parser-eval](https://github.com/jkelly-dev1/parser-eval)):
claims backed by tests, mutation checks on the tests that matter, and behavior
verified before publishing.

[ai-data-boundary-proxy](https://github.com/jkelly-dev1/ai-data-boundary-proxy)
is the closest neighbor: this repo decides what a retrieval answer may cite, and
that one decides what may leave the boundary at all, and then measures how much
of a subject survives being deleted from it.

## License

MIT
