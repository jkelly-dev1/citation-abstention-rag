"""Corpus loading and chunking.

A chunk is one `## ` section of a source document. Each chunk carries the
character offsets of its body inside the raw file, which is what lets a
verified quote resolve to an exact span in the source rather than to a
paraphrase of it.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from app.models import Chunk

FRONT_MATTER_DELIM = "---"


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
    scope = meta.get("scope", "internal")

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
