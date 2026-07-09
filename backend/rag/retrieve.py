from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from pinecone import Pinecone

from backend.rag.ingest import PINECONE_NAMESPACE


class RetrievalError(RuntimeError):
    pass


@dataclass(frozen=True)
class RetrievedChunk:
    id: str
    score: float
    chunk_text: str
    title: str | None
    heading: str | None
    jurisdiction: str | None
    category: list[str]
    policy_document_id: str | None
    chunk_index: int | None
    source_url: str | None


def retrieve_policy_chunks(
    query: str,
    *,
    category: str | list[str] | None = None,
    jurisdiction: str | None = None,
    top_k: int = 6,
) -> list[RetrievedChunk]:
    api_key = _required_env("PINECONE_API_KEY")
    index_name = _required_env("PINECONE_INDEX_NAME")
    index = Pinecone(api_key=api_key).Index(index_name)
    pinecone_filter = _metadata_filter(category=category, jurisdiction=jurisdiction)

    expanded_query = expand_query_synonyms(query)
    fields = [
        "title",
        "heading",
        "chunk_text",
        "category",
        "jurisdiction",
        "policy_document_id",
        "chunk_index",
        "source_url",
    ]

    try:
        response = index.search(
            namespace=PINECONE_NAMESPACE,
            inputs={"text": expanded_query},
            top_k=max(top_k, 10),
            filter=pinecone_filter or None,
            fields=fields,
            rerank={
                "model": "bge-reranker-v2-m3",
                "rank_fields": ["chunk_text"],
                "top_n": top_k,
            },
        )
    except Exception:
        response = index.search(
            namespace=PINECONE_NAMESPACE,
            inputs={"text": expanded_query},
            top_k=top_k,
            filter=pinecone_filter or None,
            fields=fields,
        )

    hits = _hits_from_response(response)
    return [_chunk_from_hit(hit) for hit in hits]


def expand_query_synonyms(query: str) -> str:
    expansions = {
        "pia": "Pakistan International Airlines PIA",
        "پی آئی اے": "PIA Pakistan International Airlines پاکستان انٹرنیشنل ایئرلائنز",
        "پی آئی اے": "PIA Pakistan International Airlines پاکستان انٹرنیشنل ایئرلائنز",
        "پاکستان انٹرنیشنل": "PIA Pakistan International Airlines",
        "pakistan international airlines": "PIA Pakistan International Airlines",
        "refund": "refund reimbursement involuntary refund ticket amount money back",
        "ریفنڈ": "refund reimbursement involuntary refund ticket amount money back",
        "واپسی": "refund reimbursement involuntary refund ticket amount money back",
        "cancelled": "cancelled canceled cancellation non-operation flight",
        "منسوخ": "cancelled canceled cancellation non-operation flight",
        "کینسل": "cancelled canceled cancellation non-operation flight",
        "delay": "delay delayed long delay stranded passenger",
        "تاخیر": "delay delayed long delay stranded passenger",
        "baggage": "baggage luggage bag checked baggage lost damaged",
        "سامان": "baggage luggage bag checked baggage lost damaged",
        "denied boarding": "denied boarding overbooking refused boarding volunteer",
        "airblue": "AirBlue air blue",
        "serene": "SereneAir Serene Air",
    }
    lowered = query.lower()
    additions = [
        value
        for key, value in expansions.items()
        if key in lowered
    ]
    if not additions:
        return query
    return f"{query}\n\nSynonyms and aliases: {'; '.join(additions)}"


def _metadata_filter(
    *, category: str | list[str] | None, jurisdiction: str | None
) -> dict[str, Any]:
    clauses: list[dict[str, Any]] = []
    if jurisdiction:
        clauses.append({"jurisdiction": {"$eq": jurisdiction}})
    if category:
        categories = [category] if isinstance(category, str) else category
        clauses.append({"category": {"$in": categories}})
    if not clauses:
        return {}
    if len(clauses) == 1:
        return clauses[0]
    return {"$and": clauses}


def _hits_from_response(response: Any) -> list[Any]:
    if hasattr(response, "result") and hasattr(response.result, "hits"):
        return list(response.result.hits)
    if isinstance(response, dict):
        return response.get("result", {}).get("hits", [])
    return []


def _chunk_from_hit(hit: Any) -> RetrievedChunk:
    fields = getattr(hit, "fields", None)
    if fields is None and isinstance(hit, dict):
        fields = hit.get("fields", {})
    fields = fields or {}
    hit_id = getattr(hit, "id", None) or getattr(hit, "_id", None)
    if hit_id is None and isinstance(hit, dict):
        hit_id = hit.get("_id") or hit.get("id")
    score = getattr(hit, "score", None) or getattr(hit, "_score", None)
    if score is None and isinstance(hit, dict):
        score = hit.get("_score") or hit.get("score") or 0.0

    category = fields.get("category") or []
    if isinstance(category, str):
        category = [item.strip() for item in category.split(",") if item.strip()]

    return RetrievedChunk(
        id=str(hit_id),
        score=float(score or 0.0),
        chunk_text=fields.get("chunk_text") or "",
        title=fields.get("title"),
        heading=fields.get("heading"),
        jurisdiction=fields.get("jurisdiction"),
        category=category,
        policy_document_id=fields.get("policy_document_id"),
        chunk_index=fields.get("chunk_index"),
        source_url=fields.get("source_url"),
    )


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RetrievalError(f"{name} is required")
    return value
