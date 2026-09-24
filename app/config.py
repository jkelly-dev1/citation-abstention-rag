"""Configuration. Every default is offline and deliberately conservative.

Thresholds are the levers a risk owner would actually argue about, so they are
named, documented, and settable rather than buried in the code.
"""

from __future__ import annotations

import hashlib
import json
import os
from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _env_file() -> str:
    """Support ENV_FILE=~/.secrets/ai.env so keys live outside the repo."""
    return os.path.expanduser(os.environ.get("ENV_FILE", ".env"))


class Settings(BaseSettings):
    # `env_file` IS RESOLVED PER CONSTRUCTION, NOT ONCE AT IMPORT.
    # `env_file=_env_file()` in this class body is a single expression
    # evaluated when the module is first imported, so the ENV_FILE in force at
    # import time was baked in for the life of the process: setting a
    # different ENV_FILE and constructing Settings() again silently re-read
    # the FIRST file. The documented one-shot CLI usage sets the variable
    # before the process starts and never noticed; a long-lived process that
    # switches credentials files would have read the wrong one..
    model_config = SettingsConfigDict(
        env_file_encoding="utf-8",
        extra="ignore",
    )

    def __init__(self, **values):
        super().__init__(_env_file=_env_file(), **values)

    # --- Provider ------------------------------------------------------------
    # "mock" (default, offline, deterministic), "anthropic", or "openai".
    # A provider name without its matching key falls back to the mock, so the
    # demo and the tests never depend on the network.
    agent_provider: str = "mock"
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    anthropic_model: str = "claude-opus-5"
    openai_model: str = "gpt-4o"
    agent_model: str | None = None
    max_output_tokens: int = 1500

    def model_for(self, provider: str) -> str:
        if self.agent_model:
            return self.agent_model
        return {
            "anthropic": self.anthropic_model,
            "openai": self.openai_model,
        }.get(provider, self.anthropic_model)

    # --- Corpus and retrieval ------------------------------------------------
    corpus_dir: str = "corpus"
    top_k: int = 4
    # BM25 scores are unbounded, so the reported confidence is the top score
    # divided by this saturation constant and clipped to 1.0. It is a ranking
    # heuristic, not a calibrated probability. See README.
    retrieval_saturation: float = 8.0
    min_retrieval_confidence: float = 0.35

    # --- Verification --------------------------------------------------------
    # Fraction of a claim's content words that must appear in its verified
    # quotes before the claim counts as supported.
    min_claim_coverage: float = 0.6
    # When true, every number in a claim must also appear in a verified quote.
    require_numeric_grounding: bool = True

    # --- Abstention ----------------------------------------------------------
    # Fraction of the model's claims that must survive verification before an
    # answer is served at all. Below this the system abstains rather than
    # serving a confident looking fragment of a bad answer.
    min_supported_ratio: float = 0.6
    # Fraction of the question's content words the served answer must address.
    # Guards against a grounded answer to a different question.
    min_answer_relevance: float = 0.4

    # --- Audit ---------------------------------------------------------------
    audit_log_path: str = "audit/audit.log.jsonl"
    # Optional HMAC key for the audit chain. Unset is the default and is a
    # real choice: unkeyed, the chain detects an in-place edit but not an
    # editor who recomputes the whole chain, which is integrity against
    # accident and not against an adversary. Set it from the environment or an
    # ENV_FILE outside the repository, never from a value committed here, and
    # re-chaining requires the key. See the module docstring in app/audit.py.
    audit_hmac_key: str | None = None

    # --- Eval gate -----------------------------------------------------------
    eval_min_citation_precision: float = 1.0
    eval_max_unsupported_served: int = 0
    eval_min_abstention_recall: float = 1.0
    eval_max_false_abstention_rate: float = Field(
        default=0.2,
        validation_alias=AliasChoices(
            "eval_max_false_abstention_rate", "eval_max_false_abstain_rate"
        ),
    )


    def digest(self) -> str:
        """A fingerprint of the thresholds in force, for the audit record.

        "Decided under what rules" is the auditor's second question. Without
        this, a threshold loosened between two runs would leave both records
        looking identical. Only the decision-affecting settings go in. Paths
        and keys are excluded, because a record that changed because somebody
        moved the log file would be noise, and because a digest over a secret
        is a secret's shadow.
        """
        decisive = {
            name: getattr(self, name)
            for name in sorted(type(self).model_fields)
            if name.startswith(("min_", "max_", "eval_", "require_"))
            or name in {"top_k", "retrieval_saturation", "agent_provider"}
        }
        canonical = json.dumps(decisive, sort_keys=True, separators=(",", ":"),
                               default=str)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


@lru_cache
def get_settings() -> Settings:
    return Settings()
