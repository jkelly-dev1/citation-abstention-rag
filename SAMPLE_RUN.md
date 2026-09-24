# Sample run

Verbatim captures of `scripts/run_demo.py` against all three providers, so a
reviewer without an API key can see exactly what the system does. Nothing is
edited: the offsets, hashes, scores, and model wording are the ones the runs
produced. The two real model runs were captured on 2026-07-24.

- [Offline run (mock provider)](#offline-run-mock-provider)
- [Real model run (Anthropic, claude-opus-5)](#real-model-run-anthropic-claude-opus-5)
- [Real model run (OpenAI, gpt-4o)](#real-model-run-openai-gpt-4o)
- [What the real model runs found](#what-the-real-model-runs-found)

The offline capture below is regenerated whenever the mock output changes, so
it stays byte-identical to a fresh `python scripts/run_demo.py`. The two real
model captures are not regenerated, because re-running them needs an API key
and a paid call, so they are left exactly as the 2026-07-24 runs produced them.
Their eval-gate blocks therefore predate the `model_citation_precision` metric
and do not list it, and their audit records predate the schema that now carries
a `request_id`, a UTC `ts`, a `settings_digest` and a `corpus_sha256`. Nothing
was added to them to make them look current: a capture edited to match today's
output is no longer a capture of anything.

The eval gate section at the end of each capture always runs on the mock: a
regression gate has to be reproducible, and the golden set pins which
guardrail fires for each case. The demo cases above it use whichever provider
is configured.

## Offline run (mock provider)

```
python scripts/run_demo.py
```

```
==============================================================================
citation-abstention-rag demo
provider: mock (mock-deterministic-v1)
note: cases 5 to 8 are scripted misbehavior in the mock, so each
      guardrail fires deterministically. Against a real model the
      same questions show what that model actually does.
==============================================================================

--- 1. A question the corpus answers -------------------------------
question   : Who approves an expense above 5,000 USD?
scopes     : internal, public
provider   : mock (mock-deterministic-v1)
retrieval  : confidence 1.00  [expense-policy#s01, expense-policy#s02, data-retention-standard#s04, expense-policy#s04]
status     : ANSWERED
answer     :
  - Expenses above 5,000 USD require written approval from the Chief Financial Officer before the expense is incurred.
      source: expense-policy chars 297-411
      quote : "Expenses above 5,000 USD require written approval from the Chief Financial Officer before the expense is incurred."
  - Expenses above 500 USD and up to 5,000 USD require approval from a department director.
      source: expense-policy chars 209-296
      quote : "Expenses above 500 USD and up to 5,000 USD require approval from a department director."

--- 2. A question with no answer anywhere in the corpus ------------
question   : How many people work in the Zurich office?
scopes     : internal, public
provider   : mock (mock-deterministic-v1)
retrieval  : confidence 0.27  [expense-policy#s01]
status     : ABSTAINED  (no_relevant_source)

--- 3. The answer exists, but in a document this requester is not cleared for 
question   : What revenue did Acme Holdings report for the fourth quarter?
scopes     : internal, public
provider   : mock (mock-deterministic-v1)
retrieval  : confidence 0.54  [model-risk-policy#s02, expense-policy#s02, expense-policy#s03]
status     : ABSTAINED  (answer_not_relevant)
withheld   : A model may not be used in production until an independent validator who did not build the model has signed the validation report.
             reasons: verified, but the answer was not served (coverage 1.00)
withheld   : Expense reports must be submitted within 30 days of the transaction date.
             reasons: verified, but the answer was not served (coverage 1.00)

--- 4. The same question, from a requester who is cleared for it ---
question   : What revenue did Acme Holdings report for the fourth quarter?
scopes     : internal, public, restricted
provider   : mock (mock-deterministic-v1)
retrieval  : confidence 1.00  [acme-fy2025-q4-summary#s01, acme-fy2025-q4-summary#s02, model-risk-policy#s02, expense-policy#s02]
status     : ANSWERED
answer     :
  - Acme Holdings reported revenue of 412.6 million USD for the fourth quarter of fiscal year 2025.
      source: acme-fy2025-q4-summary chars 148-243
      quote : "Acme Holdings reported revenue of 412.6 million USD for the fourth quarter of fiscal year 2025."
  - The Platform segment contributed 268.1 million USD of fourth quarter revenue.
      source: acme-fy2025-q4-summary chars 322-399
      quote : "The Platform segment contributed 268.1 million USD of fourth quarter revenue."

--- 5. The model quotes text that exists nowhere in the corpus -----
question   : What is the CEO's travel budget under the expense policy?
scopes     : internal, public
provider   : mock (mock-deterministic-v1)
retrieval  : confidence 0.68  [expense-policy#s01, expense-policy#s04, model-risk-policy#s01, expense-policy#s02]
status     : ABSTAINED  (no_supported_claims)
withheld   : The Chief Executive Officer has an annual travel budget of 250,000 USD.
             reasons: no_verified_citation (coverage 0.00)

--- 6. The model quotes a real sentence but changes the number in it 
question   : How many years are trade confirmations kept for under the standard?
scopes     : internal, public
provider   : mock (mock-deterministic-v1)
retrieval  : confidence 0.86  [data-retention-standard#s01, data-retention-standard#s02, data-retention-standard#s03, data-retention-standard#s04]
status     : ABSTAINED  (no_supported_claims)
withheld   : Trade confirmations and order records are retained for 10 years from the date of the transaction.
             reasons: ungrounded_number (coverage 0.89)

--- 7. The model cites a real chunk it was never shown -------------
question   : Who approved the Acme Q4 figure in the expense system?
scopes     : internal, public
provider   : mock (mock-deterministic-v1)
retrieval  : confidence 0.73  [expense-policy#s01, expense-policy#s02, data-retention-standard#s04, expense-policy#s04]
status     : ABSTAINED  (no_supported_claims)
withheld   : Acme reported revenue of 412.6 million USD for the fourth quarter.
             reasons: no_verified_citation (coverage 0.00)

--- 8. One grounded claim and one invented claim in the same answer 
question   : Summarize the expense approval thresholds and the CEO bonus.
scopes     : internal, public
provider   : mock (mock-deterministic-v1)
retrieval  : confidence 0.73  [expense-policy#s01, expense-policy#s02, data-retention-standard#s04, expense-policy#s04]
status     : ABSTAINED  (insufficient_support)
withheld   : The Chief Executive Officer receives a bonus of 1.2 million USD.
             reasons: no_verified_citation (coverage 0.00)
withheld   : Any single expense of 500 USD or less is approved by the employee's direct manager.
             reasons: verified, but the answer was not served (coverage 1.00)

==============================================================================
Audit trail
==============================================================================
records written : 8
chain verifies  : True

first record (truncated):
{
  "schema_version": "2",
  "request_id": "demo0000000000000000000000000001",
  "ts": "2026-01-01T00:00:00+00:00",
  "settings_digest": "9afaea19cb7c0ade",
  "corpus_sha256": "0be4ff051120709bf4f0a49a77c288f633a3372ed2f37166397d02e85e38d3ef",
  "raw_output_sha256": "a680a907f36573df6e0422c992d01941cb4a8c0be4cb67cf4c7bdec298004a56",
  "raw_output_length": 589,
  "question": "Who approves an expense above 5,000 USD?",
  "scopes": [
    "internal",
    "public"
  ],
  "provider": "mock",
  "model": "mock-deterministic-v1",
  "prompt_version": "cited-answer/v2",
  "retrieval_confidence": 1.0,
  "retrieved": [
    {
      "chunk_id": "expense-policy#s01",
      "doc_id": "expense-policy",
      "score": 16.3836,
      "doc_sha256": "f4821a3dcbe8d3d31f4f7cdcc4b665dde5f9c6c69040300fc9e3bac4b02ead1d"
    },
    {
      "chunk_id": "expense-policy#s02",
      "doc_id": "expense-policy",
      "score": 2.0277,
      "doc_sha256": "f4821a3dcbe8d3d31f4f7cdcc4b665dde5f9c6c69040300fc9e3bac4b02ead1d"
    },
    {
      "chunk_id": "data-retention-standard#s04",
      "doc_id": "data-retention-standard",
      "score": 1.7045,
      "doc_sha256": "f6a679f1b9d247e7106be3aff9c395d5c556a38669c89f757

after editing record 2 in place, chain verifies: False

==============================================================================
Eval gate (always runs on the deterministic mock provider)
==============================================================================
  cases                    16.000
  cases_passed             16.000
  citation_precision       1.000
  model_citation_precision 0.864
  unsupported_served       0.000
  bad_offsets              0.000
  abstention_recall        1.000
  false_abstention_rate    0.000

gate: PASS
```

## Real model run (Anthropic, claude-opus-5)

```
ENV_FILE=~/.secrets/ai.env AGENT_PROVIDER=anthropic python scripts/run_demo.py
```

```
==============================================================================
citation-abstention-rag demo
provider: anthropic (claude-opus-5)
note: cases 5 to 8 are the questions that make the mock misbehave.
      A real model may answer them correctly or decline; both are
      reported here as they happened.
==============================================================================

--- 1. A question the corpus answers -------------------------------
question   : Who approves an expense above 5,000 USD?
scopes     : internal, public
provider   : anthropic (claude-opus-5)
retrieval  : confidence 1.00  [expense-policy#s01, expense-policy#s02, data-retention-standard#s04, expense-policy#s04]
status     : ANSWERED
answer     :
  - An expense above 5,000 USD requires written approval from the Chief Financial Officer.
      source: expense-policy chars 297-379
      quote : "Expenses above 5,000 USD require written approval from the Chief Financial Officer"
  - That approval must be obtained before the expense is incurred.
      source: expense-policy chars 380-410
      quote : "before the expense is incurred"

--- 2. A question with no answer anywhere in the corpus ------------
question   : How many people work in the Zurich office?
scopes     : internal, public
provider   : anthropic (claude-opus-5)
retrieval  : confidence 0.27  [expense-policy#s01]
status     : ABSTAINED  (no_relevant_source)

--- 3. The answer exists, but in a document this requester is not cleared for 
question   : What revenue did Acme Holdings report for the fourth quarter?
scopes     : internal, public
provider   : anthropic (claude-opus-5)
retrieval  : confidence 0.54  [model-risk-policy#s02, expense-policy#s02, expense-policy#s03]
status     : ABSTAINED  (model_declined)

--- 4. The same question, from a requester who is cleared for it ---
question   : What revenue did Acme Holdings report for the fourth quarter?
scopes     : internal, public, restricted
provider   : anthropic (claude-opus-5)
retrieval  : confidence 1.00  [acme-fy2025-q4-summary#s01, acme-fy2025-q4-summary#s02, model-risk-policy#s02, expense-policy#s02]
status     : ANSWERED
answer     :
  - Acme Holdings reported revenue of 412.6 million USD for the fourth quarter of fiscal year 2025.
      source: acme-fy2025-q4-summary chars 148-243
      quote : "Acme Holdings reported revenue of 412.6 million USD for the fourth quarter of fiscal year 2025."
  - Of that, the Platform segment contributed 268.1 million USD and the Services segment 144.5 million USD.
      source: acme-fy2025-q4-summary chars 322-451
      quote : "The Platform segment contributed 268.1 million USD of fourth quarter revenue. The Services segment contributed 144.5 million USD."

--- 5. The model quotes text that exists nowhere in the corpus -----
question   : What is the CEO's travel budget under the expense policy?
scopes     : internal, public
provider   : anthropic (claude-opus-5)
retrieval  : confidence 0.68  [expense-policy#s01, expense-policy#s04, model-risk-policy#s01, expense-policy#s02]
status     : ABSTAINED  (model_declined)

--- 6. The model quotes a real sentence but changes the number in it 
question   : How many years are trade confirmations kept for under the standard?
scopes     : internal, public
provider   : anthropic (claude-opus-5)
retrieval  : confidence 0.86  [data-retention-standard#s01, data-retention-standard#s02, data-retention-standard#s03, data-retention-standard#s04]
status     : ANSWERED
answer     :
  - Trade confirmations are retained for 7 years from the transaction date.
      source: data-retention-standard chars 135-231
      quote : "Trade confirmations and order records are retained for 7 years from the date of the transaction."

--- 7. The model cites a real chunk it was never shown -------------
question   : Who approved the Acme Q4 figure in the expense system?
scopes     : internal, public
provider   : anthropic (claude-opus-5)
retrieval  : confidence 0.73  [expense-policy#s01, expense-policy#s02, data-retention-standard#s04, expense-policy#s04]
status     : ABSTAINED  (model_declined)

--- 8. One grounded claim and one invented claim in the same answer 
question   : Summarize the expense approval thresholds and the CEO bonus.
scopes     : internal, public
provider   : anthropic (claude-opus-5)
retrieval  : confidence 0.73  [expense-policy#s01, expense-policy#s02, data-retention-standard#s04, expense-policy#s04]
status     : ABSTAINED  (answer_not_relevant)
withheld   : The provided context contains no information about a CEO bonus, so that part of the question cannot be answered.
             reasons: claim_not_covered_by_quote (coverage 0.00)
withheld   : Any single expense of 500 USD or less is approved by the employee's direct manager.
             reasons: verified, but the answer was not served (coverage 1.00)
withheld   : Expenses above 500 USD and up to 5,000 USD require approval from a department director.
             reasons: verified, but the answer was not served (coverage 1.00)
withheld   : Expenses above 5,000 USD require written approval from the CFO before the expense is incurred.
             reasons: verified, but the answer was not served (coverage 0.90)

==============================================================================
Audit trail
==============================================================================
records written : 8
chain verifies  : True

first record (truncated):
{
  "question": "Who approves an expense above 5,000 USD?",
  "scopes": [
    "internal",
    "public"
  ],
  "provider": "anthropic",
  "model": "claude-opus-5",
  "prompt_version": "cited-answer/v2",
  "retrieval_confidence": 1.0,
  "retrieved": [
    {
      "chunk_id": "expense-policy#s01",
      "doc_id": "expense-policy",
      "score": 16.3836
    },
    {
      "chunk_id": "expense-policy#s02",
      "doc_id": "expense-policy",
      "score": 2.0277
    },
    {
      "chunk_id": "data-retention-standard#s04",
      "doc_id": "data-retention-standard",
      "score": 1.7045
    },
    {
      "chunk_id": "expense-policy#s04",
      "doc_id": "expense-policy",
      "score": 1.6821
    }
  ],
  "served_claims": [
    "An expense above 5,000 USD requires written approval from the Chief Financial Officer.",
    "That approval must be obtained before the expense is incurred."
  ],
  "dropped_claims": [],
  "status": "answered",
  "reasons": [],
  "prev_hash": "0000000000000000000000000000000000000000000000000000000000000000",
  "record_hash": "510dd2de6b6d60dfbdeea5e5f1122f1f677a9d2c58eab51436c436bbf3461c22"
}

after editing record 2 in place, chain verifies: False

==============================================================================
Eval gate (always runs on the deterministic mock provider)
==============================================================================
  cases                    13.000
  cases_passed             13.000
  citation_precision       1.000
  unsupported_served       0.000
  bad_offsets              0.000
  abstention_recall        1.000
  false_abstention_rate    0.000

gate: PASS
```

## Real model run (OpenAI, gpt-4o)

```
ENV_FILE=~/.secrets/ai.env AGENT_PROVIDER=openai python scripts/run_demo.py
```

```
==============================================================================
citation-abstention-rag demo
provider: openai (gpt-4o)
note: cases 5 to 8 are the questions that make the mock misbehave.
      A real model may answer them correctly or decline; both are
      reported here as they happened.
==============================================================================

--- 1. A question the corpus answers -------------------------------
question   : Who approves an expense above 5,000 USD?
scopes     : internal, public
provider   : openai (gpt-4o)
retrieval  : confidence 1.00  [expense-policy#s01, expense-policy#s02, data-retention-standard#s04, expense-policy#s04]
status     : ANSWERED
answer     :
  - Expenses above 5,000 USD require written approval from the Chief Financial Officer before the expense is incurred.
      source: expense-policy chars 297-411
      quote : "Expenses above 5,000 USD require written approval from the Chief Financial Officer before the expense is incurred."

--- 2. A question with no answer anywhere in the corpus ------------
question   : How many people work in the Zurich office?
scopes     : internal, public
provider   : openai (gpt-4o)
retrieval  : confidence 0.27  [expense-policy#s01]
status     : ABSTAINED  (no_relevant_source)

--- 3. The answer exists, but in a document this requester is not cleared for 
question   : What revenue did Acme Holdings report for the fourth quarter?
scopes     : internal, public
provider   : openai (gpt-4o)
retrieval  : confidence 0.54  [model-risk-policy#s02, expense-policy#s02, expense-policy#s03]
status     : ABSTAINED  (model_declined)

--- 4. The same question, from a requester who is cleared for it ---
question   : What revenue did Acme Holdings report for the fourth quarter?
scopes     : internal, public, restricted
provider   : openai (gpt-4o)
retrieval  : confidence 1.00  [acme-fy2025-q4-summary#s01, acme-fy2025-q4-summary#s02, model-risk-policy#s02, expense-policy#s02]
status     : ANSWERED
answer     :
  - Acme Holdings reported revenue of 412.6 million USD for the fourth quarter of fiscal year 2025.
      source: acme-fy2025-q4-summary chars 148-243
      quote : "Acme Holdings reported revenue of 412.6 million USD for the fourth quarter of fiscal year 2025."

--- 5. The model quotes text that exists nowhere in the corpus -----
question   : What is the CEO's travel budget under the expense policy?
scopes     : internal, public
provider   : openai (gpt-4o)
retrieval  : confidence 0.68  [expense-policy#s01, expense-policy#s04, model-risk-policy#s01, expense-policy#s02]
status     : ABSTAINED  (model_declined)

--- 6. The model quotes a real sentence but changes the number in it 
question   : How many years are trade confirmations kept for under the standard?
scopes     : internal, public
provider   : openai (gpt-4o)
retrieval  : confidence 0.86  [data-retention-standard#s01, data-retention-standard#s02, data-retention-standard#s03, data-retention-standard#s04]
status     : ANSWERED
answer     :
  - Trade confirmations are retained for 7 years.
      source: data-retention-standard chars 135-231
      quote : "Trade confirmations and order records are retained for 7 years from the date of the transaction."

--- 7. The model cites a real chunk it was never shown -------------
question   : Who approved the Acme Q4 figure in the expense system?
scopes     : internal, public
provider   : openai (gpt-4o)
retrieval  : confidence 0.73  [expense-policy#s01, expense-policy#s02, data-retention-standard#s04, expense-policy#s04]
status     : ABSTAINED  (model_declined)

--- 8. One grounded claim and one invented claim in the same answer 
question   : Summarize the expense approval thresholds and the CEO bonus.
scopes     : internal, public
provider   : openai (gpt-4o)
retrieval  : confidence 0.73  [expense-policy#s01, expense-policy#s02, data-retention-standard#s04, expense-policy#s04]
status     : ABSTAINED  (answer_not_relevant)
withheld   : Any single expense of 500 USD or less is approved by the employee's direct manager.
             reasons: verified, but the answer was not served (coverage 1.00)
withheld   : Expenses above 500 USD and up to 5,000 USD require approval from a department director.
             reasons: verified, but the answer was not served (coverage 1.00)
withheld   : Expenses above 5,000 USD require written approval from the Chief Financial Officer before the expense is incurred.
             reasons: verified, but the answer was not served (coverage 1.00)

==============================================================================
Audit trail
==============================================================================
records written : 8
chain verifies  : True

first record (truncated):
{
  "question": "Who approves an expense above 5,000 USD?",
  "scopes": [
    "internal",
    "public"
  ],
  "provider": "openai",
  "model": "gpt-4o",
  "prompt_version": "cited-answer/v2",
  "retrieval_confidence": 1.0,
  "retrieved": [
    {
      "chunk_id": "expense-policy#s01",
      "doc_id": "expense-policy",
      "score": 16.3836
    },
    {
      "chunk_id": "expense-policy#s02",
      "doc_id": "expense-policy",
      "score": 2.0277
    },
    {
      "chunk_id": "data-retention-standard#s04",
      "doc_id": "data-retention-standard",
      "score": 1.7045
    },
    {
      "chunk_id": "expense-policy#s04",
      "doc_id": "expense-policy",
      "score": 1.6821
    }
  ],
  "served_claims": [
    "Expenses above 5,000 USD require written approval from the Chief Financial Officer before the expense is incurred."
  ],
  "dropped_claims": [],
  "status": "answered",
  "reasons": [],
  "prev_hash": "0000000000000000000000000000000000000000000000000000000000000000",
  "record_hash": "59b71a53a5a142ad3a60f949b6f31145f373342221eb47f930201eb1f5e9536d"
}

after editing record 2 in place, chain verifies: False

==============================================================================
Eval gate (always runs on the deterministic mock provider)
==============================================================================
  cases                    13.000
  cases_passed             13.000
  citation_precision       1.000
  unsupported_served       0.000
  bad_offsets              0.000
  abstention_recall        1.000
  false_abstention_rate    0.000

gate: PASS
```

## What the real model runs found

Running against a real model shows what a mock cannot.

Model wording exposes stemming gaps the mock hides. The mock quotes source
sentences verbatim, so its claims and the source always share surface forms.
A model that writes `expenses` against a passage that says `expense` needs the
two to share a stem, so the stemmer strips a trailing `e` last.
`tests/test_retrieval.py::test_stemmer_unifies_common_inflections` pins it.

A collision that the threshold absorbs. Stripping the trailing `e` makes
`office` and `officer` collide, so "How many people work in the Zurich
office?" weakly matches the passage naming the Chief Financial Officer:
retrieval confidence 0.27 against a 0.35 threshold. It still abstains, which
is the intended failure direction. Pinned by
`tests/test_retrieval.py::test_a_stemmer_collision_still_lands_below_the_abstention_threshold`.

The eval gate measures the pipeline, not the model. Run against a live model,
five golden cases failed on reason codes: the model declined where the mock is
scripted to fabricate, so the guardrail that fired was `model_declined` and not
`no_supported_claims`. The system behaved correctly in every one of those
cases. That is why the gate always runs on the mock.

Both models quote verbatim. The failure mode this design most depends on
avoiding, a model that paraphrases when told to copy, did not appear. Every
citation either model served verified, and every served span in both captures
reproduces its quote from the corpus on disk.

The `citation_precision`, `unsupported_served` and `bad_offsets` lines at the
foot of each capture are not the evidence for that sentence. The eval gate
always runs on the mock, as the note at the top of this file says, so those
three numbers describe the mock run in every capture including the two real
ones. What the paragraph above rests on is the served citations printed in the
capture itself.

Neither model invented a block id. `chunk_not_retrieved` never fired against a
real model. That check earns its place from the mock and from
`tests/test_verify.py`, not from either of these runs.

Both declined cleanly when the context did not answer the question. Cases 3, 5,
and 7 came back `model_declined`, which is the model reaching the same
conclusion the verifier would have enforced anyway. Two independent refusals
have to agree before an answer is served, and here they did.

These captures predate the `model_output_unparseable` reason code, which
separates a reply the parser could not read from a model that read the
context and said no. In them, both are reported as `model_declined`. A live
run of these cases would report `model_output_unparseable` for an unreadable
reply. Nothing in the captures above was edited to match.

The scripted number substitution is a mock behavior only. Case 6 asks the mock
to quote a real sentence while changing the figure in it. Both real models
answered correctly with 7 years, so the numeric check never had to fire. It
still matters: it costs nothing when the model is honest, and
`tests/test_verify.py::test_claim_quoting_a_real_sentence_but_stating_another_number_is_unsupported`
is mutation-checked precisely because no live run can be relied on to exercise
it.

A known limitation, reproduced identically on both models. Case 8 asks two
things, one of which the corpus answers. Both models answered the expense half
correctly with three verified claims and said so plainly about the other half.
The relevance gate still abstained, because the unanswerable half's words stay
in the denominator: relevance came out at 0.33 against a 0.40 threshold. That is
over-abstention, the direction this system prefers to fail in, but it is a real
usability cost and it is not fixed. The fix is to score relevance against the
answerable portion of a question instead of the whole of it.
