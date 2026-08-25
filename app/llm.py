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
    that cannot be parsed is treated as a declined answer rather than an
    exception, so a malformed model response abstains instead of crashing.
    """
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    if not text.startswith("{"):
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end < start:
            return ModelAnswer(claims=[], model_declined=True)
        text = text[start : end + 1]
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return ModelAnswer(claims=[], model_declined=True)
    if not isinstance(payload, dict):
        return ModelAnswer(claims=[], model_declined=True)

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
        try:
            message = self._client.messages.create(
                **kwargs,
                output_config={
                    "effort": "low",
                    "format": {"type": "json_schema", "schema": ANSWER_SCHEMA},
                },
            )
        except TypeError:
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
        return response.choices[0].message.content or ""


def get_provider(settings: Settings | None = None) -> LLMProvider:
    """Return the configured provider.

    A provider name without its matching key falls back to the mock rather
    than crashing, and a key alone never selects a provider. Tests pin both
    halves of that rule.
    """
    settings = settings or get_settings()
    provider = settings.agent_provider
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
