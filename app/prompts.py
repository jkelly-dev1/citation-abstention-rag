"""Prompt construction and the JSON contract the model must answer in.

The prompt version is recorded in every audit line, so a change of wording is
visible when someone later asks why an answer looked the way it did.
"""

from __future__ import annotations

from app.models import RetrievedChunk

PROMPT_VERSION = "cited-answer/v2"

SYSTEM_PROMPT = (
    "You answer questions using ONLY the numbered context blocks provided. "
    "Break your answer into short factual claims. Every claim must carry at "
    "least one citation, and every citation must quote text copied verbatim "
    "from the block it names. Never quote text that is not in a block, and "
    "never cite a block id that was not provided. If the context does not "
    "answer the question, return an empty claims list and set declined to "
    "true. Respond with ONLY strict JSON matching the schema, with no markdown, "
    "no code fences, and no prose before or after the JSON object."
)

ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "citations": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "chunk_id": {"type": "string"},
                                "quote": {"type": "string"},
                            },
                            "required": ["chunk_id", "quote"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["text", "citations"],
                "additionalProperties": False,
            },
        },
        "declined": {"type": "boolean"},
    },
    "required": ["claims", "declined"],
    "additionalProperties": False,
}


def render_context(retrieved: list[RetrievedChunk]) -> str:
    blocks = []
    for item in retrieved:
        chunk = item.chunk
        blocks.append(
            f"[block_id: {chunk.chunk_id}] (document: {chunk.title} / {chunk.heading})\n"
            f"{chunk.text}"
        )
    return "\n\n".join(blocks)


def render_user_prompt(question: str, context: str) -> str:
    return (
        f"Context blocks:\n\n{context}\n\n"
        f"Question: {question}\n\n"
        "Return JSON of the form "
        '{"claims": [{"text": "...", "citations": [{"chunk_id": "...", '
        '"quote": "..."}]}], "declined": false}'
    )
