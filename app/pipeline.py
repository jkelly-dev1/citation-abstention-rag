"""The pipeline: retrieve, generate, verify, decide, record.

Order matters and is part of the design:

- The scope filter runs inside retrieval, before anything reaches the model.
- If retrieval is too weak, the model is never called at all. That is both the
  cheap path and the safe one, since the model cannot invent a source it was
  never shown.
- Verification runs before the decision, and the decision runs before anything
  is returned, so an unsupported claim has no path to a caller.
- The audit record is written for every request, answered or abstained.
"""

from __future__ import annotations

from app.audit import AuditLog
from app.config import Settings, get_settings
from app.corpus import cached_corpus
from app.llm import LLMProvider, get_provider, parse_model_answer
from app.models import AnswerResult, RetrievalRecord, TraceRecord
from app.policy import NO_RELEVANT_SOURCE, decide
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
) -> AnswerResult:
    settings = settings or get_settings()
    provider = provider or get_provider(settings)
    audit = audit or AuditLog(settings.audit_log_path)
    scopes = set(scopes) if scopes else set(DEFAULT_SCOPES)

    chunks = list(cached_corpus(settings.corpus_dir))
    retrieved, confidence = retrieve(question, chunks, scopes, settings)
    retrieval_records = [
        RetrievalRecord(
            chunk_id=item.chunk.chunk_id,
            doc_id=item.chunk.doc_id,
            score=round(item.score, 4),
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
        _record(audit, result, settings)
        return result

    context = render_context(retrieved)
    raw = provider.complete(question=question, context=context)
    model_answer = parse_model_answer(raw)

    by_id = {item.chunk.chunk_id: item for item in retrieved}
    verdicts = [verify_claim(claim, by_id, settings) for claim in model_answer.claims]

    decision = decide(
        confidence, verdicts, model_answer.model_declined, settings, question
    )
    result.status = decision.status
    result.reasons = decision.reasons
    result.claims = decision.served
    result.dropped = decision.dropped
    result.partial = decision.partial
    _record(audit, result, settings)
    return result


def _record(audit: AuditLog, result: AnswerResult, settings: Settings) -> None:
    audit.append(
        TraceRecord(
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
