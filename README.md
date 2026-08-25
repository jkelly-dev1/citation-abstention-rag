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
`SAMPLE_RUN.md`, along with the retrieval bug those runs found.

## The problem it addresses

Accuracy is not what makes a retrieval system usable on regulated data. The
questions that stop a deployment are these: can this answer be traced back to a
source, what happens when the model is confidently wrong, and who can show
afterwards what the system saw and decided. Waiting for a more accurate model
does not answer any of them. Architecture does.

Four controls, all implemented here:

- **Per claim attribution.** A claim is served only if a quote supporting it
  resolves to an exact span of a source document.
- **Abstention with reason codes.** Below threshold, the system refuses rather
  than guessing, and the refusal names the reason.
- Scoped retrieval. Documents outside the requester's clearance are filtered
  before scoring, so they are never ranked, never shown to the model, and never
  citable.
- **A tamper evident audit log.** Every decision is recorded, including the
  claims that were withheld and why.

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
  threshold change makes the system answer things it should refuse.
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
| Quote matching tolerates whitespace, case, and curly quotes | `tests/test_verify.py::test_quote_matching_tolerates_whitespace_case_and_curly_quotes` |
| Out of scope documents are never retrieved | `tests/test_retrieval.py::test_out_of_scope_chunks_are_never_returned` |
| The scope filter runs before scoring, not after | `tests/test_retrieval.py::test_scope_filter_runs_before_scoring` (mutation-checked: move the filter after scoring and it fails) |
| The same question answers only when the requester is cleared | `tests/test_pipeline.py::test_restricted_answer_is_withheld_without_clearance` |
| A model citing a withheld chunk gets nothing served | `tests/test_pipeline.py::test_a_citation_to_a_withheld_chunk_is_refused` |
| Weak retrieval abstains before the model is called at all | `tests/test_pipeline.py::test_unanswerable_question_abstains_without_calling_the_model` |
| A partly fabricated answer is refused whole, not trimmed and served | `tests/test_policy.py::test_too_few_supported_claims_abstains_rather_than_serving_a_fragment` (mutation-checked: set the ratio to 0 and it fails) |
| A grounded but off topic answer is refused | `tests/test_policy.py::test_grounded_but_off_topic_answer_is_refused` (mutation-checked: set relevance to 0 and it fails) |
| Unsupported claims never reach the caller | `tests/test_pipeline.py::test_unsupported_claims_never_reach_the_caller` |
| Every request writes exactly one audit record | `tests/test_pipeline.py::test_every_request_writes_one_audit_record_and_the_chain_verifies` |
| Editing, reordering, or deleting an audit record is detected | `tests/test_audit.py::test_editing_a_past_record_breaks_the_chain`, `::test_reordering_records_breaks_the_chain`, `::test_deleting_a_middle_record_breaks_the_chain` |
| The chain link itself is covered by each record's hash | `tests/test_audit.py::test_prev_hash_is_covered_by_the_record_hash` (mutation-checked: drop `prev_hash` from the hashed payload and it fails) |
| Provider selection needs both the name and the credential; keys never cross match | `tests/test_llm.py::test_provider_name_without_a_key_falls_back_to_mock`, `::test_keys_do_not_cross_match_between_providers` |
| Fenced or prose wrapped model output still parses | `tests/test_llm.py::test_parser_strips_code_fences_and_surrounding_prose` |
| Unparseable model output abstains instead of raising | `tests/test_llm.py::test_unparseable_output_declines_instead_of_raising` |
| Every unreadable model output shape declines rather than raising, including a TRUNCATED reply | `tests/test_llm.py::test_every_unreadable_shape_declines_rather_than_raising` (mutation-checked: make the JSONDecodeError branch raise and it fails) |
| The eval gate catches a real regression, not just its own thresholds | `tests/test_evals.py::test_gate_fails_when_numeric_grounding_is_disabled`, `::test_gate_fails_when_the_scope_filter_is_widened`, `::test_gate_fails_when_the_abstention_thresholds_are_removed` |
| Singular and plural forms of a word retrieve each other (regression from a real model run) | `tests/test_retrieval.py::test_stemmer_unifies_common_inflections` |
| A stemmer collision stays below the abstention threshold | `tests/test_retrieval.py::test_a_stemmer_collision_still_lands_below_the_abstention_threshold` |

## Quickstart

Requires Python 3.11 or newer. CI runs 3.11, 3.12, and 3.13.

```
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt

pytest -q                     # 62 tests, fully offline
python scripts/run_demo.py    # end to end demo over the whole corpus
python -m app.evals.gate      # the CI eval gate
```

Ask it something:

```
python -m app.cli ask "Who approves an expense above 5,000 USD?"
python -m app.cli ask "How many people work in the Zurich office?"
python -m app.cli ask "What revenue did Acme Holdings report for the fourth quarter?"
python -m app.cli ask "What revenue did Acme Holdings report for the fourth quarter?" \
  --scope internal,restricted
python -m app.cli audit-verify
```

`SAMPLE_RUN.md` holds a verbatim capture of the demo output.

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
| `model_produced_no_claims` | Empty or unparseable model output. |
| `no_supported_claims` | Every claim failed verification. |
| `insufficient_support` | Some claims survived, but too few to serve the answer. |
| `answer_not_relevant` | The surviving claims are grounded but do not address the question. |

Per citation failures are recorded separately: `chunk_not_retrieved`,
`quote_not_found_in_source`, `claim_not_covered_by_quote`, `ungrounded_number`,
`no_citation`, `no_verified_citation`.

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

- **The relevance and coverage checks are lexical, not entailment.** Token
  overlap catches a claim whose content is simply not in the quote it cites. It
  does not catch a subtle misreading of a sentence that shares its vocabulary.
  A natural language inference model or an LLM judge drops into
  `verify.claim_coverage` and `verify.answer_relevance` and nothing else in the
  pipeline changes.
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
- Numeric grounding is strict. A claim containing a number not present in its
  quotes is refused even if the rest is fine.
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
