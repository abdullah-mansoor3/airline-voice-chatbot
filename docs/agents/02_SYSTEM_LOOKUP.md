# System Lookup

Quick-reference file. Load this whenever you need to remember what a module does, how a turn
flows end to end, or what a table's columns are — instead of re-deriving it from scratch.

## Modules and what they own

| Module | Owns | Does NOT own |
|---|---|---|
| `voice/stt.py` | Parallel Groq Whisper calls (transcribe native + translate English), returns both texts + detected language; accepts optional `en`/`ur` language hints and defaults unsupported detections to Urdu | Turn-taking decisions (that's `vad.py`) |
| `voice/vad.py` | End-of-turn detection, barge-in detection, silence-threshold tuning | Audio transcription itself |
| `voice/tts.py` | Text → speech via edge-tts, voice selection by detected language | Deciding what text to speak (that's the agent) |
| `rag/chunker.py` | Clause-based splitting of policy documents by heading regex | Embedding or storage |
| `rag/ingest.py` | Creates/confirms Pinecone integrated-embedding index, embeds/upserts corpus records, mirrors metadata into `policy_chunks` / `policy_documents` | Retrieval at inference time |
| `rag/retrieve.py` | Metadata-filtered retrieval, airline/legal synonym expansion, Pinecone rerank fallback, original `chunk_text` returned for citations | Deciding what to do with retrieved chunks |
| `agent/graph.py` | LangGraph `StateGraph`: LLM tool planner → selective tool execution → validated answer | Low-level schema validation implementation |
| `agent/validation.py` | User input warning scan, JSON response schema validation, grounding check, confidence threshold, jurisdiction-match assertion — the gate before any DB write | Generating the response text |
| `agent/tools/*` | Tool implementations the agent can call (see table below) | Orchestration logic |
| `webhooks/duffel_webhook.py` | Receives Duffel push events, updates `orders` independent of any conversation | Anything conversational |
| `db/supabase_client.py` | Connection management, RLS-aware vs service-role clients | Business logic |
| `db/auth.py` | Supabase user access-token verification before WebSocket audio is processed | Login UI |
| `db/conversations.py` | Creates/resumes conversations, enforces app-side unique titles per user, and writes `messages` rows for stored history | Agent reasoning |

## Agent tools

| Tool | Backs onto | Pull/Push | Purpose |
|---|---|---|---|
| `search_policy(query, category, jurisdiction)` | Pinecone | Pull | RAG retrieval, exposed as a callable tool rather than a fixed pre-step |
| `get_order(booking_reference)` | Supabase `orders` (cached) | Pull | Fast lookup; may be seconds-to-minutes stale between webhook events |
| `get_live_order_status(duffel_order_id)` | Duffel `GET /air/orders/{id}` | Pull | Authoritative current status; slower, use when the cached row is contradicted or the decision is high-stakes |
| `search_alternative_flights(origin, destination, date)` | Duffel `POST /air/offer_requests` | Pull | Live flight availability search |

Duffel schedule changes arrive as **webhooks**, not a pull endpoint:
`order.airline_initiated_change_detected`, `order_cancellation.created`, `order.created`,
`order.creation_failed`, `payment.created`. The webhook receiver updates `orders` the moment
these fire — the agent's `get_order` call should never be older than the last real event.

## End-to-end turn flow

1. Client authenticates with Supabase Auth. The WebSocket accepts no audio/text turn until the
   first `auth` event contains a Supabase user access token that verifies successfully.
2. User can send either a text turn (`text_message`) or a voice turn (`start` + binary audio
   chunks + `stop`). Text turns never trigger TTS; voice turns do. The client may send
   `languageMode = auto | en | ur`; forced modes exist because Urdu ASR can mis-detect short
   Pakistani/code-switched utterances.
3. On voice end-of-turn: fire `transcribe` (verbose_json, native script + detected `language`)
   and `translate` (English) in parallel on the same audio buffer. If the user forced Urdu or
   English, pass the language hint into Groq and use that as the response language.
4. LangGraph receives the user query and conversation memory. A tool-planning step (Groq function
   calling) decides which tools to invoke: policy search, Duffel flight search, order lookup,
   or none for meta/memory questions. Current date/time is provided internally and appended to
   every agent response; it is not exposed as a separate planner tool. Only selected tools run.
5. The agent node always calls the LLM with whatever context was gathered (memory, datetime,
   flights, policy chunks, order context). There is no hardcoded fallback that blocks meta or
   flight-only answers when RAG returns nothing.
6. The model must return JSON matching the agent response schema. Markdown is allowed inside
   `answer_markdown`, but no prose is allowed outside the JSON envelope.
7. Retrieved citations are returned to the UI as original `chunk_text`, never translated,
   because legal clause wording can change meaning when translated. Urdu answers may explain
   the result in Urdu while the cited legal text remains in source wording.
8. Validation gate: JSON schema check → cited chunk id(s) must exist in the retrieved set **when
   policy grounding is required** → confidence ≥ threshold (~0.75) for policy answers →
   jurisdiction-match assertion → if any check fails on a policy answer, force escalation.
   Meta, datetime, and flight-only answers are not replaced by the policy fallback.
9. If the action requires a DB write (refund approval, status change): destructive actions get
   an explicit confirmation turn before executing. Write goes through the service-role
   (`sb_secret_...`) client, scoped by RLS-equivalent application logic since there's no user
   JWT session available server-side for this write.
10. For voice turns only, TTS picks the detected response language and streams sentence audio
   back over the WebSocket. Before agent work, the server speaks a short filler phrase in the
   user's language (e.g. "Ok, let me work on it" / "اچھا، ایک منٹ دیکھتا ہوں"). If the user
   speaks again while a turn is still processing, the server replies with a wait phrase instead
   of starting a new turn. Exit phrases such as "bye" or "وائس موڈ بند" end hands-free mode.
   Client and server both support `cancel` to interrupt generation/TTS.
11. Every turn's original text, English text, optional audio URL, and speaker are logged to
   `messages` under the user's `conversation_id`, regardless of whether a formal dispute has
   already been opened.
12. Conversation deletion is a backend HTTP action (`DELETE /conversations/{id}`) protected by
    the user's Supabase access token. The backend verifies ownership before deleting with the
    server-side client.
13. Frontend conversation history is explicitly filtered to the signed-in user's id. This is
    required because admin RLS may read broader data, but the normal chat UI must not show
    another account's conversations after logout/login switching.

## Database schema (Supabase / Postgres)

```sql
users (
  id uuid primary key,               -- 1:1 with auth.users
  full_name text,
  phone text,
  preferred_language text,           -- stable default, distinct from per-turn detected_language
  role text                          -- 'user' | 'admin'; admin promotion is guarded
);

conversations (
  id uuid primary key,
  user_id uuid references users(id),
  title text,
  status text,
  primary_language text,
  started_at timestamp,
  last_message_at timestamp,
  unique (user_id, title)
);

orders (
  id uuid primary key,
  user_id uuid references users(id),
  duffel_order_id text,              -- Duffel's own order id, source of truth for live data
  order_type text,
  amount numeric,
  fare_class text,
  status text                        -- kept fresh by the Duffel webhook receiver
);

disputes (
  id uuid primary key,
  user_id uuid references users(id),
  order_id uuid references orders(id),
  conversation_id uuid references conversations(id),
  claim_type text,
  detected_language text,            -- actual per-conversation reality, vs users.preferred_language
  status text,
  created_at timestamp
);

messages (
  id uuid primary key,
  conversation_id uuid references conversations(id),
  dispute_id uuid references disputes(id), -- nullable until a dispute is opened/classified
  turn_index int,
  speaker text,                      -- 'user' | 'agent'
  original_text text,                -- native-script transcript
  english_text text,                  -- from the parallel translate call
  audio_url text,
  created_at timestamp
);

dispute_actions (
  id uuid primary key,
  dispute_id uuid references disputes(id),
  cited_chunk_id uuid references policy_chunks(id),
  action_type text,                  -- approve_refund | reject | request_info | escalate
  refund_amount numeric,
  executed_by text                   -- 'ai' | 'human_override'
  -- insert-only: a human override is a new row linked to the prior one, never an UPDATE
);

-- optional join table if a decision needs to cite more than one clause:
dispute_action_citations (
  action_id uuid references dispute_actions(id),
  chunk_id uuid references policy_chunks(id),
  primary key (action_id, chunk_id)
);

policy_documents (
  id uuid primary key,
  title text,
  version text,
  effective_date date,                -- populate this at ingestion, not just created_at
  jurisdiction text,                  -- 'PK' | 'US' | 'international'
  category text                       -- e.g. 'customer_refund', 'crew_duty_rest'
);

policy_chunks (
  id uuid primary key,
  policy_document_id uuid references policy_documents(id),
  pinecone_vector_id text,
  chunk_index int,
  chunk_text text
);
```

Cardinality reminders (crow's foot): `users ||--o{ conversations`, `users ||--o{ orders`,
`users ||--o{ disputes`, `conversations ||--o{ messages`, `conversations ||--o{ disputes`,
`disputes ||--o{ messages`, `disputes ||--o{ dispute_actions`,
`policy_documents ||--o{ policy_chunks`, `policy_chunks ||--o{ dispute_actions`.

## Pinecone index

One index (Starter tier caps you at 5, and category/jurisdiction filtering makes multiple
indexes unnecessary). Metadata fields on every vector: `category`, `jurisdiction`,
`policy_document_id`, `chunk_index`, `version`, `effective_date`. Embedded text is prefixed
with a breadcrumb (e.g. `"PCAA ANO-001-ATCP-2.0 § D13 Cancellation — "`) before embedding —
this is deliberate, not decorative (see `03_RAG_STRATEGY.md`).

Current verified index: `airline-policy-corpus`, Pinecone integrated embedding model
`multilingual-e5-large`, dimension 1024, metric `cosine`, status `Ready`.
