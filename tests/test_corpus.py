"""Chunking and, more importantly, the offsets that make citations checkable."""

from __future__ import annotations

from pathlib import Path

from app.config import Settings
from app.corpus import cached_corpus, UNLABELED_SCOPE, load_corpus
from app.pipeline import DEFAULT_SCOPES
from app.retrieval import retrieve
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


def test_every_shipped_document_carries_an_explicit_scope_label(chunks):
    """The fail-closed default below must stay a safety net, not a load path.

    If a shipped document ever relies on the default, the demo silently stops
    serving it and the next reader debugs retrieval instead of the front
    matter.
    """
    assert chunks
    assert all(chunk.scope != UNLABELED_SCOPE for chunk in chunks)


def test_a_document_with_no_scope_label_is_withheld_rather_than_served(tmp_path):
    """Forgetting a `scope:` line must not publish the document.

    Defaulting to a label inside DEFAULT_SCOPES ("internal", say) would serve
    a document whose front matter has no scope line to every ordinary
    requester, and the document would read completely normally.

    Mutation check: set the default in `app/corpus.py` to "internal" and this
    goes red at the retrieval assertion.
    """
    (tmp_path / "unlabeled.md").write_text(
        "---\n"
        "doc_id: unlabeled-doc\n"
        "title: Board Compensation Working Notes\n"
        "---\n\n"
        "## Compensation\n\n"
        "The Chief Executive Officer receives a retention award of 3.4 million USD.\n",
        encoding="utf-8",
    )
    chunks = load_corpus(tmp_path)
    assert chunks, "the fixture document must actually chunk"
    assert all(chunk.scope == UNLABELED_SCOPE for chunk in chunks)

    settings = Settings(corpus_dir=str(tmp_path), audit_log_path=str(tmp_path / "a.jsonl"))
    retrieved, confidence = retrieve(
        "What retention award does the Chief Executive Officer receive?",
        chunks,
        set(DEFAULT_SCOPES),
        settings,
    )
    assert retrieved == []
    assert confidence == 0.0


def test_corpus_check_fails_when_a_document_answers_nobody(tmp_path, monkeypatch, capsys):
    """A document with no `scope:` is withheld from everyone, and silently.

    The only symptom today is that questions it should answer come back
    `no_relevant_source`, and the next reader debugs retrieval instead of front
    matter. `corpus-check` turns a silent fail-closed into a visible one.

    Mutation check: make `_corpus_check` return 0 unconditionally and this goes
    red.
    """
    import app.cli as cli
    from app.config import Settings

    (tmp_path / "good.md").write_text(
        "---\ndoc_id: good\ntitle: Good\nscope: internal\nversion: 1.0\n---\n"
        "## Heading\nA sentence that is retrievable.\n", encoding="utf-8")
    settings = Settings(corpus_dir=str(tmp_path))
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    cached_corpus.cache_clear()
    assert cli.main(["corpus-check"]) == 0

    (tmp_path / "orphan.md").write_text(
        "---\ndoc_id: orphan\ntitle: Orphan\nversion: 2.0\n---\n"
        "## Heading\nThis document names no scope at all.\n", encoding="utf-8")
    cached_corpus.cache_clear()
    assert cli.main(["corpus-check"]) == 1
    captured = capsys.readouterr()
    assert "orphan" in captured.err
    assert "WITHHELD FROM EVERY REQUESTER" in captured.out
    cached_corpus.cache_clear()


def test_every_document_carries_its_version_and_a_digest_of_its_whole_file(chunks):
    """Each chunk carries its document's `version:` and a whole-file digest.

    The digest is over the whole file, front matter included, so a `scope:`
    line edited to widen who may see a document changes it. That is exactly
    the change an auditor needs to detect, and a body-only digest misses it.

    Mutation check: digest only the body and this goes red.
    """
    assert chunks
    for chunk in chunks:
        assert chunk.version, f"{chunk.doc_id} has no version"
        assert len(chunk.doc_sha256) == 64

    import hashlib
    from pathlib import Path as _P
    first = chunks[0]
    on_disk = _P(first.source_path).read_text(encoding="utf-8")
    assert first.doc_sha256 == hashlib.sha256(on_disk.encode("utf-8")).hexdigest()
    # The front matter is inside it: changing only the scope line moves it.
    import re as _re
    widened, count = _re.subn(r"(?m)^scope:.*$", "scope: everyone", on_disk, count=1)
    assert count == 1, f"{first.doc_id} has no scope line to widen"
    assert hashlib.sha256(widened.encode("utf-8")).hexdigest() != first.doc_sha256
