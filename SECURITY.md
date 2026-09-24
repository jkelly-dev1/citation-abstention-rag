# Security Policy

## Reporting a Vulnerability

Please report suspected vulnerabilities privately, not through public issues or
pull requests.

Preferred channel: use GitHub's private vulnerability reporting for this
repository (the "Report a vulnerability" button on the Security tab). It opens a
private advisory visible only to the maintainer.

Aim is to acknowledge a report within 5 business days and to share a resolution
or mitigation plan within 30 days. Timelines may vary, as this is maintained in
personal time.

Please include enough detail to reproduce the issue: the affected file or
endpoint, the version or commit, steps to reproduce, and the impact you observed.

## Supported Versions

This is a personal learning and portfolio project. Only the latest commit on the
`main` branch is supported; there are no maintained release branches or
backports.

## Threat model for the audit log

The audit log is hash chained, and what that detects depends on one setting.

With no `AUDIT_HMAC_KEY` (the default), records are chained with a plain
SHA-256. This detects an edit, a reordering, or a deletion made in place: every
hash after the change stops matching. It does not detect an editor who changes
a record and then recomputes the rest of the chain, because the digest is one
anyone can compute. Treat the unkeyed chain as integrity against corruption and
accident, not against an adversary with write access to the file.

With `AUDIT_HMAC_KEY` set, records are chained with an HMAC and re-chaining
requires the key. An adversary with the log but not the key cannot produce a
file that verifies. This only helps if the key is held somewhere they cannot
reach from wherever they reached the log: an environment secret, a KMS, a
separate host. Choosing and protecting that location is out of scope here, so
the default is unkeyed and no key is committed to the repository.

Neither mode protects against an adversary who can delete or replace the whole
file and is never asked for the previous head hash. Anchoring the head
somewhere append-only is the standard answer and is not implemented.

Both behaviors are pinned by tests in `tests/test_audit.py`, including the
unkeyed weakness, so the limitation cannot be quietly outgrown by the README.

Appends take an exclusive `flock` on the log file, so two writers cannot read
the same head hash and fork the chain. `flock` is advisory and POSIX-only: a
writer that does not ask is not stopped, and this is a guard against ordinary
concurrency, not a substitute for an append-only store.

## Threat model for the corpus

Verification proves provenance, not truth. A served claim is checked
against a quote, and that quote is checked against the chunk the retriever
actually returned. Nothing in this system checks whether the corpus is
correct. A document dropped into `corpus/` saying the opposite of policy is
retrieved, quoted and served exactly like any other, with a resolvable
citation attached. `corpus/` is therefore the trust boundary, and whoever can
write there decides what this system will say.

What the system does give an auditor is the means to notice afterwards. Each
chunk carries the `version:` from its front matter and a SHA-256 of the whole
document, front matter included, so a `scope:` line edited to widen who may
see a document changes the digest. Every audit record carries the digest of
the corpus the request actually retrieved from, alongside a digest of the
decision thresholds in force. `python -m app.cli audit-show N` re-opens each
cited span on disk and reports whether it still reproduces the stored quote,
exiting nonzero when it does not.

Controlling write access to `corpus/`, and holding the document digests
somewhere the same writer cannot reach, is out of scope here for the same
reason the HMAC key is.

## Scope

These are self-contained demo projects, not production services. Provider
credentials are supplied by the operator at runtime and are never committed to
the repository. Limitations that the README documents as deliberate,
out-of-scope seams are noted but may not be actioned.
