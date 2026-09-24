"""The pipeline: retrieve, generate, verify, decide, record.

Order matters and is part of the design:

- The scope filter runs inside retrieval, before anything reaches the model.
- If retrieval is too weak, the model is never called at all. That is both the
  cheap path and the safe one, since the model cannot invent a source it was
  never shown.
- Verification runs before the decision, and the decision runs before anything
  is returned, so an unsupported claim has no path to a caller.
- The audit record is written for every request, answered or abstained,
  including one whose provider raised. That is the request a reader most
  wants to find in the log.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone

from app.audit import AuditLog
from app.config import Settings, get_settings
from app.corpus import cached_corpus, corpus_digest
from app.llm import LLMProvider, get_provider, parse_model_answer
from app.models import (
    RECORD_SCHEMA_VERSION,
    AnswerResult,
    RetrievalRecord,
    TraceRecord,
)
from app.policy import NO_RELEVANT_SOURCE, PROVIDER_ERROR, decide
from app.prompts import PROMPT_VERSION, render_context
from app.retrieval import retrieve
from app.verify import verify_claim

DEFAULT_SCOPES = frozenset({"public", "internal"})


def answer_question(
    question: str,
    scopes: set[str] | frozenset[str] | None = None,
    settings: Settings | None = None,
    provider: LLMProvider | None = None,
    audit: AuditLog | None = None,
    request_id: str | None = None,
    now: str | None = None,
) -> AnswerResult:
    settings = settings or get_settings()
    provider = provider or get_provider(settings)
    audit = audit or AuditLog(settings.audit_log_path, settings.audit_hmac_key)
    # None means the caller did not state a clearance, so the demo default
    # applies. An EMPTY set means the caller stated one and it is empty: a
    # requester cleared for nothing. Those are opposite answers, and
    # collapsing them with a truthiness test promotes a failed entitlement
    # lookup (the ordinary way such a lookup fails) into full clearance.
    scopes = set(DEFAULT_SCOPES) if scopes is None else set(scopes)

    # One identity and one clock reading per request, taken here and not
    # inside `_record`, so the timestamp is when the request was served and
    # not when the writer happened to get to it.
    # The caller may supply both, and two different callers need to.
    # A real deployment propagates a trace id from upstream instead of
    # minting a fresh one per hop, and a capture needs both to be fixed: the
    # demo's output is committed to SAMPLE_RUN.md and compared byte for byte,
    # so a random id and a wall clock reading make it impossible to reproduce.
    # Defaulting to a fresh uuid and the real clock keeps ordinary calls
    # unchanged.
    request_id = request_id or uuid.uuid4().hex
    started = now or datetime.now(timezone.utc).isoformat(timespec="seconds")
    chunks = list(cached_corpus(settings.corpus_dir))
    corpus_sha256 = corpus_digest(chunks)
    retrieved, confidence = retrieve(question, chunks, scopes, settings)
    retrieval_records = [
        RetrievalRecord(
            chunk_id=item.chunk.chunk_id,
            doc_id=item.chunk.doc_id,
            score=round(item.score, 4),
            doc_sha256=item.chunk.doc_sha256,
        )
        for item in retrieved
    ]

    result = AnswerResult(
        status="abstained",
        question=question,
        scopes=sorted(scopes),
        retrieval_confidence=round(confidence, 4),
        retrieved=retrieval_records,
        provider=provider.name,
        model=provider.model,
    )

    if confidence < settings.min_retrieval_confidence:
        # Nothing worth grounding an answer in. Do not call the model.
        result.reasons = [NO_RELEVANT_SOURCE]
        _record(audit, result, settings, request_id=request_id, ts=started,
                corpus_sha256=corpus_sha256)
        return result

    context = render_context(retrieved)
    try:
        raw = provider.complete(question=question, context=context)
    except Exception as exc:  # noqa: BLE001 - see below
        # A provider failure is an abstention, not a crash, and it gets an
        # audit record like every other request. Without this branch the
        # exception would leave `answer_question`, `_record` below would never
        # run, and the failed request would be the one with no log entry.
        #
        # The except is broad because each SDK raises its own hierarchy
        # (anthropic.APIStatusError, openai.APIError, plus socket and TLS
        # errors from underneath them), and this repository imports neither
        # SDK unless a real provider was selected, so it cannot name those
        # classes here without loading them. A narrower tuple would let every
        # class it failed to name crash the request. The exception is not
        # swallowed: its type and message go into the reason code and
        # therefore into the audit record.
        result.reasons = [PROVIDER_ERROR, f"{type(exc).__name__}: {exc}"]
        _record(audit, result, settings, request_id=request_id, ts=started,
                corpus_sha256=corpus_sha256)
        return result
    model_answer = parse_model_answer(raw)

    by_id = {item.chunk.chunk_id: item for item in retrieved}
    verdicts = [verify_claim(claim, by_id, settings) for claim in model_answer.claims]

    decision = decide(
        confidence,
        verdicts,
        model_answer.model_declined,
        settings,
        question,
        parse_error=model_answer.parse_error,
    )
    result.status = decision.status
    result.reasons = decision.reasons
    result.claims = decision.served
    result.dropped = decision.dropped
    result.partial = decision.partial
    _record(audit, result, settings, request_id=request_id, ts=started,
            corpus_sha256=corpus_sha256, raw=raw)
    return result


def _record(
    audit: AuditLog,
    result: AnswerResult,
    settings: Settings,
    *,
    request_id: str = "",
    ts: str = "",
    corpus_sha256: str = "",
    raw: str | None = None,
) -> None:
    # The raw reply is fingerprinted, not stored. It is untrusted model output
    # and this log is read by people; the length and the digest are enough to
    # tell two different failures apart and to match a record against a
    # captured transcript, without putting the text itself in the record.
    raw_sha = hashlib.sha256(raw.encode("utf-8")).hexdigest() if raw is not None else ""
    audit.append(
        TraceRecord(
            schema_version=RECORD_SCHEMA_VERSION,
            request_id=request_id,
            ts=ts,
            settings_digest=settings.digest(),
            corpus_sha256=corpus_sha256,
            raw_output_sha256=raw_sha,
            raw_output_length=len(raw) if raw is not None else 0,
            question=result.question,
            scopes=result.scopes,
            provider=result.provider,
            model=result.model,
            prompt_version=PROMPT_VERSION,
            retrieval_confidence=result.retrieval_confidence,
            retrieved=result.retrieved,
            served_claims=result.claims,
            dropped_claims=result.dropped,
            status=result.status,
            reasons=result.reasons,
        )
    )
