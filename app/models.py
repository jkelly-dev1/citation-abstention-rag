"""Typed data model for the retrieval, verification, and decision pipeline.

Every stage produces one of these objects, and the audit record is assembled
from them, so what the API returns and what the audit log records cannot drift
apart.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Chunk(BaseModel):
    """One retrievable passage, with byte offsets back into its source file."""

    chunk_id: str
    doc_id: str
    title: str
    scope: str
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
    """The parsed model response. Nothing here is trusted yet."""

    claims: list[Claim] = Field(default_factory=list)
    model_declined: bool = False


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
    provider: str = "mock"
    model: str = "mock-deterministic-v1"

    @property
    def answer_text(self) -> str:
        return " ".join(claim.text for claim in self.claims)


class TraceRecord(BaseModel):
    """One append-only audit line. Hash fields are filled in by the audit log."""

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
