---
doc_id: model-risk-policy
title: Model Risk Management Policy
scope: internal
version: 1.3
---

## Scope of the policy

This policy covers every quantitative model used to support a business
decision, including statistical models, machine learning models, and language
model applications that generate output a person acts on.

## Validation before use

A model may not be used in production until an independent validator who did
not build the model has signed the validation report. The validation report
records the intended use, the data the model was trained or grounded on, the
known limitations, and the monitoring plan.

## Human review of consequential output

Output that informs a client facing recommendation, a credit decision, or a
regulatory filing must be reviewed by a qualified person before it is used.
The reviewer records their name and the date of review.

## Attribution and traceability

Any model output presented as fact must be traceable to a source record. If a
model cannot produce a source for a statement, the statement is treated as
unverified and must not be presented to a client.

## Annual review

Every model in the inventory is reviewed at least annually. A model that has
not been reviewed within 13 months is suspended from production use until the
review is complete.
