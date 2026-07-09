from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, TypeVar

from pinecone import Pinecone
from supabase import Client

from backend.db.supabase_client import get_service_supabase_client
from backend.rag.chunker import PolicyChunk, PolicyDocument, chunk_policy_file

PINECONE_NAMESPACE = "__default__"
EMBED_MODEL = "multilingual-e5-large"
INDEX_DIMENSION = 1024


class IngestionError(RuntimeError):
    pass


def ensure_pinecone_index() -> str:
    api_key = _required_env("PINECONE_API_KEY")
    index_name = _required_env("PINECONE_INDEX_NAME")
    cloud = os.getenv("PINECONE_CLOUD", "aws")
    region = os.getenv("PINECONE_REGION", "us-east-1")

    pc = Pinecone(api_key=api_key)
    if not pc.has_index(index_name):
        pc.create_index_for_model(
            name=index_name,
            cloud=cloud,
            region=region,
            embed={
                "model": EMBED_MODEL,
                "field_map": {"text": "embedded_text"},
            },
            deletion_protection="disabled",
            tags={"project": "airline-dispute-agent"},
        )

    return index_name


def ingest_policy_corpus(corpus_dir: str | Path = "docs/files") -> dict[str, int]:
    index_name = ensure_pinecone_index()
    pc = Pinecone(api_key=_required_env("PINECONE_API_KEY"))
    index = pc.Index(index_name)
    supabase = get_service_supabase_client()

    files = sorted(Path(corpus_dir).glob("*.md"))
    document_count = 0
    chunk_count = 0

    for path in files:
        if path.name.startswith("README"):
            continue
        document, chunks = chunk_policy_file(path)
        policy_document_id = _upsert_policy_document(supabase, document)
        records = [
            _pinecone_record(policy_document_id, chunk)
            for chunk in chunks
        ]
        for batch in _batched(records, 96):
            index.upsert_records(namespace=PINECONE_NAMESPACE, records=batch)
        _upsert_policy_chunks(supabase, policy_document_id, chunks)
        document_count += 1
        chunk_count += len(chunks)

    return {"documents": document_count, "chunks": chunk_count}


def _upsert_policy_document(client: Client, document: PolicyDocument) -> str:
    payload = {
        "title": document.title,
        "version": document.version,
        "effective_date": document.effective_date,
        "jurisdiction": document.jurisdiction,
        "category": ",".join(document.category),
    }

    existing = (
        client.table("policy_documents")
        .select("id")
        .eq("title", document.title)
        .eq("jurisdiction", document.jurisdiction)
        .limit(1)
        .execute()
    )
    if existing.data:
        document_id = existing.data[0]["id"]
        client.table("policy_documents").update(payload).eq("id", document_id).execute()
        return document_id

    response = client.table("policy_documents").insert(payload).execute()
    if not response.data:
        raise IngestionError(f"Could not upsert policy document: {document.title}")
    return response.data[0]["id"]


def _upsert_policy_chunks(
    client: Client,
    policy_document_id: str,
    chunks: list[PolicyChunk],
) -> None:
    rows = [
        {
            "policy_document_id": policy_document_id,
            "pinecone_vector_id": _vector_id(policy_document_id, chunk.chunk_index),
            "chunk_index": chunk.chunk_index,
            "chunk_text": chunk.chunk_text,
        }
        for chunk in chunks
    ]
    existing = (
        client.table("policy_chunks")
        .select("id,chunk_index")
        .eq("policy_document_id", policy_document_id)
        .execute()
    )
    existing_by_index = {
        int(row["chunk_index"]): row["id"]
        for row in (existing.data or [])
    }

    inserts: list[dict] = []
    for row in rows:
        existing_id = existing_by_index.get(int(row["chunk_index"]))
        if existing_id:
            client.table("policy_chunks").update(row).eq("id", existing_id).execute()
        else:
            inserts.append(row)

    for batch in _batched(inserts, 200):
        client.table("policy_chunks").insert(batch).execute()


def _pinecone_record(policy_document_id: str, chunk: PolicyChunk) -> dict:
    vector_id = _vector_id(policy_document_id, chunk.chunk_index)
    metadata = _pinecone_metadata({
        **chunk.metadata,
        "policy_document_id": policy_document_id,
        "chunk_index": chunk.chunk_index,
        "chunk_text": chunk.chunk_text,
        "embedded_text": chunk.embedded_text,
    })
    return {"_id": vector_id, **metadata}


def _pinecone_metadata(metadata: dict) -> dict:
    return {
        key: value
        for key, value in metadata.items()
        if value is not None
    }


def _vector_id(policy_document_id: str, chunk_index: int) -> str:
    return f"{policy_document_id}:{chunk_index}"


T = TypeVar("T")


def _batched(items: Iterable[T], size: int) -> Iterable[list[T]]:
    batch: list[T] = []
    for item in items:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise IngestionError(f"{name} is required")
    return value


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    result = ingest_policy_corpus()
    print(result)
