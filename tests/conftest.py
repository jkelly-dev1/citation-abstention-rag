from __future__ import annotations

from pathlib import Path

import pytest

from app.audit import AuditLog
from app.config import Settings
from app.corpus import cached_corpus

REPO_ROOT = Path(__file__).resolve().parents[1]
CORPUS_DIR = str(REPO_ROOT / "corpus")


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Offline settings with an isolated audit log. Never touches the network."""
    return Settings(
        agent_provider="mock",
        anthropic_api_key=None,
        openai_api_key=None,
        corpus_dir=CORPUS_DIR,
        audit_log_path=str(tmp_path / "audit.jsonl"),
    )


@pytest.fixture
def audit(settings: Settings) -> AuditLog:
    return AuditLog(settings.audit_log_path)


@pytest.fixture
def chunks():
    return list(cached_corpus(CORPUS_DIR))


@pytest.fixture
def chunk_by_id(chunks):
    return {chunk.chunk_id: chunk for chunk in chunks}
