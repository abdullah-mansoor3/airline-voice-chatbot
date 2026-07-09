from __future__ import annotations

from dataclasses import asdict

from backend.rag.retrieve import RetrievedChunk, retrieve_policy_chunks


async def search_policy(
    query: str,
    category: str | list[str] | None = None,
    jurisdiction: str | None = None,
    top_k: int = 6,
) -> list[dict]:
    chunks: list[RetrievedChunk] = retrieve_policy_chunks(
        query,
        category=category,
        jurisdiction=jurisdiction,
        top_k=top_k,
    )
    return [asdict(chunk) for chunk in chunks]
