"""Corpus loading and chunking.

A chunk is one `## ` section of a source document. Each chunk carries the
character offsets of its body inside the raw file, which is what lets a
verified quote resolve to an exact span in the source rather than to a
paraphrase of it.
"""

from __future__ import annotations

from functools import lru_cache
import hashlib
from pathlib import Path

from app.models import Chunk

FRONT_MATTER_DELIM = "---"

#: The scope a document gets when its front matter does not name one.
#: No requester is expected to hold this label, so an unlabeled document is
#: withheld from everyone until somebody classifies it. Defaulting to a real
#: clearance instead would mean that forgetting a `scope:` line silently
#: publishes the document to every requester who holds that clearance, and
#: the failure would be invisible because the document reads normally.
UNLABELED_SCOPE = "unlabeled"


def _parse_front_matter(raw: str) -> tuple[dict[str, str], int]:
    """Return (metadata, offset where the body starts) for a corpus file."""
    if not raw.startswith(FRONT_MATTER_DELIM):
        return {}, 0
    end = raw.find(f"\n{FRONT_MATTER_DELIM}", len(FRONT_MATTER_DELIM))
    if end == -1:
        return {}, 0
    block = raw[len(FRONT_MATTER_DELIM) : end]
    meta: dict[str, str] = {}
    for line in block.splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()
    body_start = end + len(FRONT_MATTER_DELIM) + 1
    return meta, body_start


def chunk_document(raw: str, path: Path) -> list[Chunk]:
    meta, body_start = _parse_front_matter(raw)
    doc_id = meta.get("doc_id", path.stem)
    title = meta.get("title", path.stem)
    scope = meta.get("scope", UNLABELED_SCOPE)
    # `version:` is in the front matter of every shipped document, and a
    # reader who bumps it has every reason to expect it in the trace.
    version = meta.get("version", "")
    # The digest is over the whole file, front matter included. A scope line
    # edited to widen who can see a document is exactly the change an auditor
    # needs to detect, and a digest over the body alone would miss it.
    doc_sha256 = hashlib.sha256(raw.encode("utf-8")).hexdigest()

    # Collect heading positions so each section's body offsets are exact.
    headings: list[tuple[int, str]] = []
    cursor = body_start
    while True:
        index = raw.find("\n## ", cursor - 1 if cursor else 0)
        if index == -1:
            break
        line_end = raw.find("\n", index + 1)
        if line_end == -1:
            line_end = len(raw)
        headings.append((index + 1, raw[index + 4 : line_end].strip()))
        cursor = line_end + 1

    chunks: list[Chunk] = []
    for position, (heading_start, heading) in enumerate(headings):
        body_offset = raw.find("\n", heading_start) + 1
        next_start = (
            headings[position + 1][0] if position + 1 < len(headings) else len(raw)
        )
        text = raw[body_offset:next_start].strip()
        if not text:
            continue
        start = raw.find(text, body_offset)
        chunks.append(
            Chunk(
                chunk_id=f"{doc_id}#s{position + 1:02d}",
                doc_id=doc_id,
                title=title,
                scope=scope,
                version=version,
                doc_sha256=doc_sha256,
                heading=heading,
                text=text,
                source_path=str(path),
                start=start,
                end=start + len(text),
            )
        )
    return chunks


def load_corpus(corpus_dir: str | Path) -> list[Chunk]:
    directory = Path(corpus_dir)
    chunks: list[Chunk] = []
    for path in sorted(directory.glob("*.md")):
        chunks.extend(chunk_document(path.read_text(encoding="utf-8"), path))
    return chunks


@lru_cache
def cached_corpus(corpus_dir: str) -> tuple[Chunk, ...]:
    return tuple(load_corpus(corpus_dir))


def corpus_digest(chunks) -> str:
    """One digest over every document the corpus holds, order-independent.

    Built from the per-document sha256 values instead of by re-reading the
    directory, so the record says what the request actually retrieved from and
    not what happens to be on disk when somebody asks later. Sorted, so two
    runs over the same documents agree whatever order the loader produced.
    """
    digests = sorted({chunk.doc_sha256 for chunk in chunks})
    return hashlib.sha256("".join(digests).encode("utf-8")).hexdigest()
