from __future__ import annotations

from dataclasses import asdict

from backend.rag.retrieve import RetrievedChunk, retrieve_policy_chunks


async def search_policy(
    query: str,
    category: str | list[str] | None = None,
    jurisdiction: str | None = None,
    top_k: int = 6,
) -> list[dict]:
    if isinstance(category, list):
        chunks: list[RetrievedChunk] = []
        for cat in category:
            chunks.extend(retrieve_policy_chunks(
                query,
                category=cat,
                jurisdiction=jurisdiction,
                top_k=top_k,
            ))
        # Deduplicate and sort by score
        seen = set()
        unique_chunks = []
        for c in sorted(chunks, key=lambda x: x.score, reverse=True):
            if c.id not in seen:
                seen.add(c.id)
                unique_chunks.append(c)
        chunks = unique_chunks[:top_k]
    else:
        chunks: list[RetrievedChunk] = retrieve_policy_chunks(
            query,
            category=category,
            jurisdiction=jurisdiction,
            top_k=top_k,
        )
    return [asdict(chunk) for chunk in chunks]
