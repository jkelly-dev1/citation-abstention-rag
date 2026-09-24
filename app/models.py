"""Typed data model for the retrieval, verification, and decision pipeline.

Every stage produces one of these objects, and the audit record is assembled
from them, so what the API returns and what the audit log records cannot drift
apart.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Chunk(BaseModel):
    """One retrievable passage, with byte offsets back into its source file.

    `version` and `doc_sha256` identify WHICH TEXT a citation resolved against.
    Verification proves a served claim came from the corpus; it does not prove
    the corpus is right, and it cannot prove the corpus has not changed since.
    Without these, a record says a span of a path and a reader auditing it
    later has no way to tell whether the file still holds what was cited.
    """

    chunk_id: str
    doc_id: str
    title: str
    scope: str
    version: str
    doc_sha256: str
    heading: str
    text: str
    source_path: str
    start: int
    end: int


class RetrievedChunk(BaseModel):
    chunk: Chunk
    score: float


class Citation(BaseModel):
    """A citation as the model produced it: a chunk id plus a claimed quote."""

    chunk_id: str
    quote: str


class Claim(BaseModel):
    """One assertion the model wants to make, with the citations it offers."""

    text: str
    citations: list[Citation] = Field(default_factory=list)


class ModelAnswer(BaseModel):
    """The parsed model response. Nothing here is trusted yet.

    `model_declined` and `parse_error` are different facts and are kept
    apart. The first is the model saying the context does not answer the
    question, which is the system working. The second is the reply being
    unreadable (a length cap that truncated the JSON, a refusal in prose, an
    empty body), which is the system being unable to look. Collapsing them
    into one flag gives both the same reason code in the audit log, and an
    operator triaging that log can no longer tell a working abstention from a
    broken one.
    """

    claims: list[Claim] = Field(default_factory=list)
    model_declined: bool = False
    #: True when the reply could not be parsed at all. Mutually exclusive with
    #: `model_declined`: an unreadable reply never claims the model declined.
    parse_error: bool = False


class VerifiedCitation(BaseModel):
    """A citation after the system checked it against the retrieved text."""

    chunk_id: str
    quote: str
    ok: bool
    reason: str | None = None
    doc_id: str | None = None
    source_path: str | None = None
    # Character offsets of the quote inside the source document, so a caller
    # can highlight the exact span a claim rests on.
    start: int | None = None
    end: int | None = None


class ClaimVerdict(BaseModel):
    text: str
    supported: bool
    coverage: float
    citations: list[VerifiedCitation] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)


class RetrievalRecord(BaseModel):
    chunk_id: str
    doc_id: str
    score: float
    doc_sha256: str = ""


class AnswerResult(BaseModel):
    """What the caller gets back. Unsupported claims never appear in `claims`."""

    status: Literal["answered", "abstained"]
    question: str
    scopes: list[str]
    claims: list[ClaimVerdict] = Field(default_factory=list)
    dropped: list[ClaimVerdict] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    partial: bool = False
    retrieval_confidence: float = 0.0
    retrieved: list[RetrievalRecord] = Field(default_factory=list)

    def for_caller(self) -> "AnswerResult":
        """The same result with the withheld text removed.

        README says a production caller gets reason codes only and the
        withheld text stays in the audit log. This object's `dropped` carries
        the full text of every claim the policy refused, so anything shown to
        a caller, including `--json`, goes through this first. An answer the
        system decided not to stand behind is exactly the text a caller should
        not be quoting.

        The reasons survive. A caller still needs to know that something was
        withheld and why, so each dropped claim keeps its reason codes and its
        coverage and loses only `text` and the quotes inside its citations.
        """
        redacted = self.model_copy(deep=True)
        for claim in redacted.dropped:
            claim.text = ""
            for citation in claim.citations:
                citation.quote = ""
        return redacted
    provider: str = "mock"
    model: str = "mock-deterministic-v1"

    @property
    def answer_text(self) -> str:
        return " ".join(claim.text for claim in self.claims)


#: The shape of a hashed record. A record's hash is computed over its whole
#: payload, so adding a field changes every hash, and a log written before
#: the change then fails to recompute under the new code. That is not tamper
#: evidence, it is a schema boundary, and reporting it as "AUDIT CHAIN BROKEN"
#: tells an operator their log was altered when it was not. Records carry the
#: version that wrote them so the two can be told apart.
#: Records written before this field existed parse as "1" by default.
RECORD_SCHEMA_VERSION = "2"


class TraceRecord(BaseModel):
    """One append-only audit line. Hash fields are filled in by the audit log.

    An auditor asks first "decided when, and under what rules". A record
    without a time, without the thresholds in force and without a corpus
    digest answers neither, however faithfully it records the decision
    itself. `raw_output_sha256` and `raw_output_length` exist for the one case
    where the reply is discarded: a parse failure abstains with no claims,
    and without these the record of the failure says nothing about what came
    back. The reply itself is not stored, because it is untrusted model output
    and the log is read by people, so the record carries its fingerprint and
    its size instead.
    """

    schema_version: str = ""
    request_id: str = ""
    ts: str = ""
    settings_digest: str = ""
    corpus_sha256: str = ""
    raw_output_sha256: str = ""
    raw_output_length: int = 0
    question: str
    scopes: list[str]
    provider: str
    model: str
    prompt_version: str
    retrieval_confidence: float
    retrieved: list[RetrievalRecord] = Field(default_factory=list)
    served_claims: list[ClaimVerdict] = Field(default_factory=list)
    dropped_claims: list[ClaimVerdict] = Field(default_factory=list)
    status: str = "abstained"
    reasons: list[str] = Field(default_factory=list)
    prev_hash: str = ""
    record_hash: str = ""

    def payload_for_hash(self) -> dict:
        """Everything except `record_hash` itself, including `prev_hash`."""
        payload = self.model_dump(mode="json")
        payload.pop("record_hash", None)
        return payload
