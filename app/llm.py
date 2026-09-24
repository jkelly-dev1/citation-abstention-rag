"""Provider seam: one Protocol, three implementations.

- MockProvider: deterministic and offline. It answers extractively from the
  context it is given, and it scripts a set of realistic failure modes keyed
  off the question text so the verification and abstention guardrails are
  exercised without a network call.
- AnthropicProvider / OpenAIProvider: real paths, SDK imported lazily and only
  when both the provider name and its API key are present.

Every provider returns raw text. Parsing is tolerant and lives in
`parse_model_answer`, because a real model sometimes wraps JSON in fences or
prose no matter what the system prompt says.
"""

from __future__ import annotations

import json
import re
from typing import Protocol, runtime_checkable

from app.config import Settings, get_settings
from app.models import Citation, Claim, ModelAnswer
from app.prompts import ANSWER_SCHEMA, SYSTEM_PROMPT, render_user_prompt

_BLOCK = re.compile(
    r"\[block_id: (?P<chunk_id>[^\]]+)\][^\n]*\n(?P<text>.*?)(?=\n\n\[block_id: |\Z)",
    re.DOTALL,
)
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


@runtime_checkable
class LLMProvider(Protocol):
    name: str
    model: str

    def complete(self, *, question: str, context: str) -> str:
        """Return the model's raw text response for one question."""
        ...


def parse_model_answer(raw: str) -> ModelAnswer:
    """Tolerantly parse a model response into the typed answer.

    Strips code fences and any prose surrounding the JSON object. A response
    that cannot be parsed produces an abstention instead of an exception, so a
    malformed model response never crashes the pipeline.

    An unreadable reply sets `parse_error`, not `model_declined`. The two are
    different events and the abstention policy gives them different reason
    codes: `model_output_unparseable` means the system could not read the
    model, `model_declined` means the model read the context and said no.
    """
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    if not text.startswith("{"):
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end < start:
            return ModelAnswer(claims=[], parse_error=True)
        text = text[start : end + 1]
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return ModelAnswer(claims=[], parse_error=True)
    if not isinstance(payload, dict):
        return ModelAnswer(claims=[], parse_error=True)

    claims: list[Claim] = []
    for entry in payload.get("claims") or []:
        if not isinstance(entry, dict):
            continue
        citations = []
        for citation in entry.get("citations") or []:
            if isinstance(citation, dict) and citation.get("chunk_id"):
                citations.append(
                    Citation(
                        chunk_id=str(citation.get("chunk_id")),
                        quote=str(citation.get("quote") or ""),
                    )
                )
        claims.append(Claim(text=str(entry.get("text") or ""), citations=citations))
    return ModelAnswer(claims=claims, model_declined=bool(payload.get("declined")))


def _parse_blocks(context: str) -> list[tuple[str, str]]:
    return [
        (match.group("chunk_id"), match.group("text").strip())
        for match in _BLOCK.finditer(context)
    ]


def _sentences(text: str) -> list[str]:
    return [
        " ".join(sentence.split())
        for sentence in _SENTENCE.split(text)
        if sentence.strip()
    ]


class MockProvider:
    """Deterministic stand-in. Extractive by default, scripted on demand.

    This does not simulate a model well. It is to make retrieval,
    verification, abstention, the audit trail, and the eval gate fully
    testable offline, including the failure modes that matter.
    """

    name = "mock"

    #: question substring -> scripted behavior. Each one exists to drive a
    #: specific guardrail; the tests assert the guardrail, not the script.
    SCRIPTS = {
        # Cites a real chunk id that was withheld by the scope filter.
        "who approved the acme q4 figure": "cite_unretrieved",
        # Quotes text that does not exist in any source.
        "what is the ceo's travel budget": "fabricate_quote",
        # Quotes a real sentence but states a different number.
        "how many years are trade confirmations kept": "wrong_number",
        # Half the claims are fabricated: below the supported ratio.
        "summarize the expense approval thresholds and the ceo bonus": "half_fabricated",
        # Quotes a real sentence and NEGATES it: the polarity check.
        "is written approval required above 5,000 usd": "negate",
        # Returns bytes that are not a readable reply at all. The trigger is
        # part of a question that retrieves well. A trigger that only matches
        # an unanswerable question never reaches the model, because the
        # pipeline abstains at no_relevant_source before the provider is
        # called, and the script would then demonstrate nothing.
        "trade confirmations retained in full": "truncate",
        # Reads the context and says no, which is not the same as failing.
        "approves an expense above 5,000 usd, precisely": "decline",
    }

    def __init__(self, model: str = "mock-deterministic-v1") -> None:
        self.model = model

    def complete(self, *, question: str, context: str) -> str:
        blocks = _parse_blocks(context)
        script = next(
            (
                behavior
                for trigger, behavior in self.SCRIPTS.items()
                if trigger in question.lower()
            ),
            None,
        )
        if script == "cite_unretrieved":
            return json.dumps(
                {
                    "claims": [
                        {
                            "text": "Acme reported revenue of 412.6 million USD for the fourth quarter.",
                            "citations": [
                                {
                                    "chunk_id": "acme-fy2025-q4-summary#s01",
                                    "quote": "Acme Holdings reported revenue of 412.6 million USD",
                                }
                            ],
                        }
                    ],
                    "declined": False,
                }
            )
        if script == "fabricate_quote":
            chunk_id = blocks[0][0] if blocks else "expense-policy#s01"
            return json.dumps(
                {
                    "claims": [
                        {
                            "text": "The Chief Executive Officer has an annual travel budget of 250,000 USD.",
                            "citations": [
                                {
                                    "chunk_id": chunk_id,
                                    "quote": "The Chief Executive Officer has an annual travel budget of 250,000 USD.",
                                }
                            ],
                        }
                    ],
                    "declined": False,
                }
            )
        if script == "wrong_number":
            grounded = self._extract(question, blocks, limit=1)
            quote = grounded[0]["citations"][0]["quote"] if grounded else ""
            chunk_id = grounded[0]["citations"][0]["chunk_id"] if grounded else ""
            return json.dumps(
                {
                    "claims": [
                        {
                            "text": "Trade confirmations and order records are retained for 10 years from the date of the transaction.",
                            "citations": [{"chunk_id": chunk_id, "quote": quote}],
                        }
                    ],
                    "declined": False,
                }
            )
        if script == "half_fabricated":
            claims = self._extract(question, blocks, limit=1)
            claims.append(
                {
                    "text": "The Chief Executive Officer receives a bonus of 1.2 million USD.",
                    "citations": [
                        {
                            "chunk_id": blocks[0][0] if blocks else "expense-policy#s01",
                            "quote": "The Chief Executive Officer receives a bonus of 1.2 million USD.",
                        }
                    ],
                }
            )
            return json.dumps({"claims": claims, "declined": False})

        if script == "negate":
            # A real quote, a real chunk, and the opposite meaning. The
            # overlap fraction cannot see this one, because the claim keeps
            # almost all of the quote's vocabulary and reverses it.
            grounded = self._extract(question, blocks, limit=1)
            if grounded:
                quote = grounded[0]["citations"][0]["quote"]
                chunk_id = grounded[0]["citations"][0]["chunk_id"]
                negated = quote.replace(" require ", " do not require ", 1)
                if negated == quote:
                    negated = "It is not the case that " + quote
                return json.dumps(
                    {
                        "claims": [
                            {"text": negated,
                             "citations": [{"chunk_id": chunk_id, "quote": quote}]}
                        ],
                        "declined": False,
                    }
                )
        if script == "truncate":
            # A length cap landing mid-object is the most ordinary real-model
            # failure there is, and it must produce an abstention.
            return '{"claims": [{"text": "Expenses above 5,000 USD requ'
        if script == "decline":
            # The model read the context and said no. An honest decline is a
            # different fact from a reply nobody could read, and the two get
            # different reason codes.
            return json.dumps({"claims": [], "declined": True})

        claims = self._extract(question, blocks, limit=2)
        return json.dumps({"claims": claims, "declined": not claims})

    @staticmethod
    def _extract(question: str, blocks: list[tuple[str, str]], limit: int) -> list[dict]:
        """Quote the sentences from context that best match the question."""
        from app.retrieval import content_tokens

        question_words = content_tokens(question)
        scored: list[tuple[int, str, str]] = []
        for chunk_id, text in blocks:
            for sentence in _sentences(text):
                overlap = len(content_tokens(sentence) & question_words)
                if overlap:
                    scored.append((overlap, chunk_id, sentence))
        scored.sort(key=lambda item: (-item[0], item[1], item[2]))
        return [
            {
                "text": sentence,
                "citations": [{"chunk_id": chunk_id, "quote": sentence}],
            }
            for _, chunk_id, sentence in scored[:limit]
        ]


def rejects_output_config(exc: Exception) -> bool:
    """True for both ways `output_config` is refused.

    The fallback in `AnthropicProvider.complete` covers "an SDK or model that
    does not accept output_config", and those are two different failures. A
    stale SDK has no such keyword argument and raises TypeError client-side,
    before any request is sent. A model that will not take the parameter
    answers over the wire with HTTP 400, which is not a TypeError.

    Neither SDK class is named here. This module imports anthropic and openai
    lazily, only when that provider is selected, and naming
    `anthropic.BadRequestError` in a module-level except clause would load the
    SDK for everyone, breaking the README's promise that mock mode loads
    neither (a test pins that). `status_code` is the attribute both SDKs put
    on their HTTP errors, so asking for it needs no import.

    The test stays narrow. Retrying on any exception would re-send the
    request on an auth failure, a rate limit or a timeout, turning one failed
    call into two and charging for both. 400 is the only status that means
    "this request shape is not acceptable", and that is the only thing the
    fallback can repair.
    """
    if isinstance(exc, TypeError):
        return True
    return getattr(exc, "status_code", None) == 400


class HostileProvider:
    """A provider that attacks the verifier on every question.

    The claim this repository makes is that the policy cannot serve an
    unsupported claim. A mock that behaves well except on a few scripted
    questions demonstrates the guardrails on those questions only. This one
    tries to break them on every question, and the count of unsupported claims
    served should still be zero.

    It is not reachable by accident. `AGENT_PROVIDER=hostile` has to be asked
    for by name, exactly like a real provider, and it needs no key. For that
    reason `get_provider` refuses an unknown name instead of falling through
    to the mock: a typo that silently downgraded to the honest mock would make
    an adversarial run report zero because nothing attacked it.

    Five attacks at once, one per known verifier surface:
      1. a quote that appears in no source            -> quote_not_found_in_source
      2. a chunk id the retriever never returned      -> chunk_not_retrieved
      3. a real quote with its polarity reversed      -> negation_mismatch
      4. a real quote with its figure changed         -> ungrounded_number
      5. a second citation on a real claim, fabricated
    """

    name = "hostile"

    def __init__(self, model: str = "hostile-adversary-v1") -> None:
        self.model = model

    def complete(self, *, question: str, context: str) -> str:
        blocks = _parse_blocks(context)
        chunk_id = blocks[0][0] if blocks else "expense-policy#s01"
        real = MockProvider()._extract(question, blocks, limit=1)
        claims = [
            {
                "text": "The Chief Executive Officer has an annual travel budget "
                        "of 250,000 USD.",
                "citations": [{"chunk_id": chunk_id,
                               "quote": "The Chief Executive Officer has an annual "
                                        "travel budget of 250,000 USD."}],
            },
            {
                "text": "Acme reported revenue of 412.6 million USD for the fourth "
                        "quarter.",
                "citations": [{"chunk_id": "acme-fy2025-q4-summary#s01",
                               "quote": "Acme Holdings reported revenue of 412.6 "
                                        "million USD"}],
            },
        ]
        if real:
            quote = real[0]["citations"][0]["quote"]
            source = real[0]["citations"][0]["chunk_id"]
            negated = quote.replace(" require ", " do not require ", 1)
            if negated == quote:
                negated = "It is not the case that " + quote
            claims.append({"text": negated,
                           "citations": [{"chunk_id": source, "quote": quote}]})
            import re as _re
            swapped = _re.sub(r"\b(\d[\d,]*)\b", "999", quote, count=1)
            claims.append({"text": swapped,
                           "citations": [{"chunk_id": source, "quote": quote}]})
            claims.append({
                "text": real[0]["text"],
                "citations": [
                    {"chunk_id": source, "quote": quote},
                    {"chunk_id": source, "quote": "and the board must countersign it"},
                ],
            })
        return json.dumps({"claims": claims, "declined": False})


class AnthropicProvider:
    """Real Anthropic path. The SDK is imported lazily, only when selected."""

    name = "anthropic"

    def __init__(self, api_key: str, model: str, max_tokens: int) -> None:
        try:
            import anthropic  # noqa: F401
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError(
                "AGENT_PROVIDER=anthropic but the 'anthropic' package is not "
                "installed. Run: pip install anthropic"
            ) from exc
        from anthropic import Anthropic

        self._client = Anthropic(api_key=api_key)
        self.model = model
        self.max_tokens = max_tokens

    def complete(self, *, question: str, context: str) -> str:  # pragma: no cover - needs a live key
        kwargs = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": SYSTEM_PROMPT,
            "messages": [
                {"role": "user", "content": render_user_prompt(question, context)}
            ],
        }
        # Prefer schema-constrained output; fall back to instruction-only on an
        # SDK or model that does not accept output_config. This parser is
        # tolerant either way, which is what a real run needs.
        # `rejects_output_config` decides which failures a retry can repair:
        # a client-side TypeError from an SDK without the keyword, and an
        # HTTP 400 from a model that will not take it. Nothing else.
        try:
            message = self._client.messages.create(
                **kwargs,
                output_config={
                    "effort": "low",
                    "format": {"type": "json_schema", "schema": ANSWER_SCHEMA},
                },
            )
        except Exception as exc:  # noqa: BLE001 - re-raised unless it is a refusal of the kwarg
            if not rejects_output_config(exc):
                raise
            message = self._client.messages.create(**kwargs)
        if getattr(message, "stop_reason", None) == "refusal":
            return json.dumps({"claims": [], "declined": True})
        return "".join(
            block.text for block in message.content if getattr(block, "type", "") == "text"
        )


class OpenAIProvider:
    """Real OpenAI path. The SDK is imported lazily, only when selected."""

    name = "openai"

    def __init__(self, api_key: str, model: str, max_tokens: int) -> None:
        try:
            import openai  # noqa: F401
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError(
                "AGENT_PROVIDER=openai but the 'openai' package is not "
                "installed. Run: pip install openai"
            ) from exc
        from openai import OpenAI

        self._client = OpenAI(api_key=api_key)
        self.model = model
        self.max_tokens = max_tokens

    def complete(self, *, question: str, context: str) -> str:  # pragma: no cover - needs a live key
        response = self._client.chat.completions.create(
            model=self.model,
            max_completion_tokens=self.max_tokens,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": render_user_prompt(question, context)},
            ],
        )
        # An empty `choices` list is a legitimate response shape, so the
        # first element is not indexed blindly. Returning "" hands the empty
        # string to `parse_model_answer`, which sets `parse_error`, and the
        # policy files it as `model_output_unparseable`: an abstention with a
        # reason code, the same outcome as any other reply it cannot read.
        if not response.choices:
            return ""
        return response.choices[0].message.content or ""


def get_provider(settings: Settings | None = None) -> LLMProvider:
    """Return the configured provider.

    A provider name without its matching key falls back to the mock rather
    than crashing, and a key alone never selects a provider. Tests pin both
    halves of that rule.
    """
    settings = settings or get_settings()
    # The name is case-folded, so `AGENT_PROVIDER=Anthropic` selects the
    # Anthropic provider. An unknown name is refused, not mapped to the mock:
    # a name nobody recognizes is a configuration error, and guessing which
    # provider the operator meant is not this function's job.
    provider = (settings.agent_provider or "").strip().lower()
    if provider not in {"mock", "hostile", "anthropic", "openai"}:
        raise ValueError(
            f"AGENT_PROVIDER={settings.agent_provider!r} is not a provider. "
            f"Use one of: mock, anthropic, openai."
        )
    if provider == "hostile":
        return HostileProvider()
    if provider == "anthropic" and settings.anthropic_api_key:
        return AnthropicProvider(
            settings.anthropic_api_key,
            settings.model_for("anthropic"),
            settings.max_output_tokens,
        )
    if provider == "openai" and settings.openai_api_key:
        return OpenAIProvider(
            settings.openai_api_key,
            settings.model_for("openai"),
            settings.max_output_tokens,
        )
    return MockProvider()
