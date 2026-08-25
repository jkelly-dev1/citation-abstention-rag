"""Chunking and, more importantly, the offsets that make citations checkable."""

from __future__ import annotations

from pathlib import Path

from app.corpus import load_corpus
from tests.conftest import CORPUS_DIR


def test_chunks_carry_scope_and_document_metadata(chunk_by_id):
    chunk = chunk_by_id["expense-policy#s01"]
    assert chunk.doc_id == "expense-policy"
    assert chunk.scope == "internal"
    assert chunk.heading == "Approval thresholds"
    assert "5,000 USD" in chunk.text


def test_restricted_document_is_labelled_restricted(chunk_by_id):
    assert chunk_by_id["acme-fy2025-q4-summary#s01"].scope == "restricted"


def test_chunk_offsets_reproduce_the_chunk_text_exactly():
    # Offsets are the whole basis of a verifiable citation: if they drift, a
    # citation points at the wrong span of the source document.
    for chunk in load_corpus(CORPUS_DIR):
        raw = Path(chunk.source_path).read_text(encoding="utf-8")
        assert raw[chunk.start : chunk.end] == chunk.text


def test_front_matter_is_not_part_of_any_chunk(chunks):
    assert not any("doc_id:" in chunk.text for chunk in chunks)
