---
doc_id: incident-response-runbook
title: Security Incident Response Runbook
scope: internal
version: 3.0
---

## Severity definitions

A Severity 1 incident is a confirmed compromise of production data or a
complete outage of a client facing service. A Severity 2 incident is a
suspected compromise or a degraded client facing service. Everything else is
Severity 3.

## Notification timing

The on call responder acknowledges a Severity 1 page within 15 minutes. The
incident commander notifies the Legal and Compliance leads within 1 hour of
declaring a Severity 1 incident.

## Evidence handling

Evidence is collected before remediation where collection does not extend the
outage. Disk and memory images are stored in the incident evidence bucket with
write once retention enabled.

## Post incident review

A written post incident review is published within 10 business days of closing
a Severity 1 incident. The review names the contributing causes and the
corrective actions with an owner and a due date for each.
