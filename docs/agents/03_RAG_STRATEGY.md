# RAG Strategy — Ingestion and Inference

This is the settled strategy. Don't redesign it mid-project without updating
`06_DECISIONS_LOG.md` and explaining why the original reasoning stopped applying.

Since the move to an agentic architecture, retrieval is exposed to the LangGraph agent as the
`search_policy(query, category, jurisdiction)` tool rather than a fixed pre-generation step —
the strategy below is unchanged, only *when* it's invoked has changed (the agent decides,
possibly more than once per turn, possibly alongside other tool calls).

## Ingestion

Current implementation note (2026-07-09): `backend/rag/ingest.py` uses Pinecone integrated
embedding with `multilingual-e5-large`. Records include `embedded_text` for embedding and
separate original `chunk_text` for display/citation. `chunk_text` must remain untranslated in
the UI, including when the answer language is Urdu.

**1. Chunk by clause, not by character count.**
Every document in the corpus has natural legal units (Rule N, Article N, § N, D-section).
A generic 512-token sliding-window chunker will cut a compensation formula in half.

- Split on the heading regex per doc type (`## RULE \d+`, `#### § \d+`, `### D\d+`) so each
  chunk = one clause.
- If a clause chunk exceeds the embedding model's comfortable range (~500 tokens for
  multilingual-e5), sub-split at the lettered/numbered sub-clause level (A/B/C or 1/2/3),
  never mid-sentence.
- Keep frontmatter metadata (`category`, `carrier`/`regulator`, `document_type`, `source_url`)
  attached to every chunk, not just the file — this feeds `policy_chunks` metadata directly
  and is what lets `dispute_actions.cited_chunk_id` resolve to something human-readable later.
- Prepend a short breadcrumb to each chunk's embedded text itself (e.g.
  `"PCAA ANO-001-ATCP-2.0 § D13 Cancellation — "`) before embedding. This measurably improves
  retrieval precision because the clause number and document identity become part of what's
  being matched, not just metadata sitting alongside it.

**2. One embedding index, metadata-filtered — not five separate indexes.**
Pinecone Starter caps you at 5 indexes, and the actual need (scope by category depending on
who's asking) is better served by one index with `category` as a metadata field, filtered at
query time (`category: {"$in": [...]}`) based on which persona is asking (customer/crew/
operator). This is strictly better than five indexes: cross-category retrieval (a customer
claim that also needs the Montreal Convention's SDR figures) still works, and the free
embedding-token and write-unit budget isn't fragmented across mostly-idle indexes.

**3. Ingest jurisdiction as its own filterable field, separate from category.**
The corpus mixes PK and US documents. Add `jurisdiction: PK | US | international`. This is
what prevents comparing a PIA claim against Part 250 instead of the ANO — filter by
jurisdiction first, category second, at retrieval time, rather than relying on the LLM to
notice the mismatch itself.

**4. Version every document.**
`policy_documents` has room for this — ingestion must actually populate `effective_date`, not
just `created_at`. PCAA revises ANOs regularly, and carriers update their COCs on a rolling
basis. Storing `effective_date` per version means a re-litigated dispute gets checked against
the policy as it stood on the booking date. This is the entire reason `policy_documents` mirrors
Pinecone in Postgres in the first place — don't let ingestion silently skip populating it.

## Inference (retrieval + arbitration)

Current implementation note (2026-07-09): retrieval is implemented in `backend/rag/retrieve.py`
with Pinecone metadata filters, airline/legal synonym expansion, Pinecone rerank fallback, and
original clause text returned for display/citation. The current classifier in `agent/graph.py`
is heuristic; the order-aware classification call described below is still a future hardening
step.

**1. Two-stage retrieval, not one-shot top-k.**
- Stage 1: classify the claim's `category` (refund/baggage/crew/operator) and `jurisdiction`
  (PK/US/international) from the transcribed claim + an `orders` lookup (carrier tells you
  jurisdiction; claim type tells you category). This is a cheap classification call, not a
  retrieval call.
- Stage 2: retrieve top-k (k=5-8) within that metadata filter. This is what prevents a
  Pakistani customer's cancellation claim from pulling Delta's Rule 19 instead of PCAA's D13,
  just because Delta's text is more verbose and embeds "confidently."

**2. Always retrieve the treaty alongside the carrier contract for international-jurisdiction
claims.**
For any claim on an international itinerary, retrieve both the carrier's own COC clause and
the relevant Montreal Convention article, and instruct the arbitration LLM to check the
carrier clause isn't more restrictive than the treaty floor (Article 26 makes such clauses
void). This is a real arbitration pattern, not a nice-to-have.

**3. Expand common airline/legal synonyms before embedding search.**
User utterances will not reliably match corpus wording. Current expansion covers high-value
aliases and bilingual variants such as:

- `PIA`, `Pakistan International Airlines`, `پی آئی اے`, `پی آئی اے`, `پاکستان انٹرنیشنل`
- refund / ریفنڈ / واپسی
- cancel / cancelled / cancellation / منسوخ / کینسل
- delay / تاخیر
- baggage / luggage / سامان
- AirBlue / air blue / ائربلو, Serene / Serene Air / سیرین

This is intentionally conservative query expansion, not a replacement for filtered retrieval.
Keep it close to the corpus and expected Pakistani airline vocabulary so it improves recall
without flooding the query with unrelated terms.

**4. Rerank after metadata-filtered retrieval when Pinecone supports it.**
`retrieve_policy_chunks` asks Pinecone for more candidates than the final top-k, then applies
Pinecone rerank with `bge-reranker-v2-m3` over `chunk_text`. If rerank is unavailable on the
current Pinecone plan/API response, the code falls back to normal integrated-embedding search
instead of failing the user turn. This gives better ordering for Urdu/code-switched claims
while keeping the prototype robust.

**5. The validation gate stays exactly as designed — don't loosen it for multi-doc categories.**
Cited chunk id must exist in the retrieved top-k, confidence gate ~0.75 cosine, escalate below
threshold. One addition given overlapping jurisdictions now sit in one index: add a
jurisdiction-match assertion — if `orders.carrier` is Pakistani but the top-cited chunk's
`jurisdiction` metadata is US, force escalation regardless of similarity score. This catches
retrieval errors a pure cosine threshold won't.

**6. Cite the chunk id, don't let the model paraphrase the number.**
Several documents (Southwest §7, PCAA D12.3, Part 250 §250.5) have jurisdiction-specific
dollar/percentage figures that look superficially similar. The arbitration system prompt must
explicitly instruct: *quote the exact figure from the cited chunk; do not compute or estimate
a compensation amount from general knowledge of airline policy.* This is a cheap guard against
the model blending a memorized "typical" formula with the actual jurisdiction-correct one.

## Corpus sources

- Pakistani carriers: PIA, Serene Air, AirBlue conditions of carriage / baggage policy
- International carriers (denser, DOT-mandated detail): Delta domestic + international COC,
  plus the travelersunited.org hub for other US majors
- Pakistan regulator: PCAA Air Navigation Orders (Airworthiness + Security directorate hubs)
- US regulator (crew/operator depth): FAA 14 CFR Part 117 (duty/rest), Part 121 Subparts R/S
- Treaty: Montreal Convention (Article 26 — carrier clauses can't undercut the treaty floor)
- FAA 14 CFR Part 250 (oversales/denied boarding compensation)
