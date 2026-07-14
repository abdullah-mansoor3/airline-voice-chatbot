# Project Bootstrap Prompt

Give this file to a coding agent (Claude Code, Cursor, etc.) as the first message in a new session.

---

## What this project is

A bilingual (Urdu/English/code-switched) dispute resolution agent for airline customer claims
(refunds, cancellations, delay compensation). The user can type a claim or speak it. Voice
turns are transcribed and answered with text + TTS; text turns are answered with text only.
The system retrieves the relevant policy clause (airline ToS, aviation regulator rules, or the
Montreal Convention depending on jurisdiction), reasons over it with an LLM agent that can
also pull live flight data, validates the answer/action against hard rules, and replies in the
user's own language.

Read `docs/agents/02_SYSTEM_LOOKUP.md` before writing any code — it has the module map, the full
turn-by-turn flow, and the database schema. Read `docs/agents/03_RAG_STRATEGY.md` before touching
ingestion or retrieval code. Read `docs/agents/06_DECISIONS_LOG.md` before changing any architectural
choice that looks arbitrary — it probably isn't.

## Target user

General Pakistani population calling in about a flight dispute — may speak Urdu, English, or
switch between both mid-sentence. Do not assume English-only input anywhere in the pipeline.

## Confirmed tech stack

| Layer | Choice | Notes |
|---|---|---|
| STT | `whisper-large-v3-turbo` via Groq | Fire `transcribe` (native script + language) and `translate` (English) in parallel on the same audio |
| LLM / agent | `openai/gpt-oss-120b` on Groq (fallback `qwen/qwen3.6-27b`) | Groq deprecated `llama-3.3-70b-versatile` — don't use it |
| Orchestration | LangGraph | `StateGraph` with an `agent` node and a `tools` node, looping via conditional edge |
| TTS | `edge-tts` (unofficial, free, no key) | `ur-PK-UzmaNeural` / `ur-PK-AsadNeural` for Urdu, any `en-US` neural voice for English |
| Vector DB | Pinecone Starter (free) | One index, metadata-filtered by `category` and `jurisdiction` |
| App DB / auth | Supabase (Postgres + Auth) | See schema in `02_SYSTEM_LOOKUP.md` |
| Live flight data | Duffel API | `POST /air/offer_requests` (search), `GET /air/orders` (status), webhooks for schedule changes |
| Backend | Python: FastAPI + WebSockets | Async throughout — this is a real-time voice pipeline |
| Frontend | Next.js | Web Audio API for mic capture (`echoCancellation`, `noiseSuppression`, `autoGainControl` all `true`), WebSocket client |

## Current prototype state (2026-07-09)

The project has moved beyond the first walking skeleton:

- Login is required before text/voice turns.
- The UI is LLM-style chat with text input, mic input, conversation history, delete controls,
  Markdown rendering, and optional hands-free voice conversation.
- Voice turns can use Auto / Force English / Force Urdu language mode. Unsupported detections
  are normalized to Urdu because the product only supports English and Urdu.
- RAG ingestion and retrieval are live against Pinecone index `airline-policy-corpus`.
- Retrieval includes metadata filters, conservative airline/legal synonym expansion, and
  Pinecone rerank fallback.
- Agent output is validated through a JSON envelope before display; destructive DB writes are
  still future work and require a confirmation turn plus audit trail.

## Directory structure to create

```
airline-dispute-agent/
├── .env                        # not committed
├── .env.example                # committed, no real values
├── README.md
├── docs/agents/ #not committed
│   ├── 01_PROJECT_CONTEXT_AND_BOOTSTRAP.md
│   ├── 02_SYSTEM_LOOKUP.md
│   ├── 03_RAG_STRATEGY.md
│   ├── 04_PROGRESS_AND_FEATURES.md
│   ├── 05_ENV_AND_SECRETS.md
│   └── 06_DECISIONS_LOG.md
├── backend/
│   ├── requirements.txt
│   ├── server.py                # FastAPI app + WebSocket endpoint
│   ├── voice/
│   │   ├── stt.py               # parallel transcribe + translate calls
│   │   ├── tts.py               # edge-tts wrapper, voice selection by language
│   │   └── vad.py               # turn-detection / endpointing
│   ├── rag/
│   │   ├── chunker.py           # clause-based chunking
│   │   ├── ingest.py            # embed + upsert to Pinecone + mirror to Postgres
│   │   └── retrieve.py          # filtered retrieval + rerank
│   ├── agent/
│   │   ├── graph.py             # LangGraph StateGraph definition
│   │   ├── validation.py        # grounding + confidence + jurisdiction-match gate
│   │   └── tools/
│   │       ├── policy_search.py
│   │       ├── orders.py
│   │       ├── duffel_client.py
│   │       └── flight_search.py
│   ├── webhooks/
│   │   └── duffel_webhook.py    # receives order.airline_initiated_change_detected etc.
│   └── db/
│       ├── schema.sql
│       └── supabase_client.py
├── frontend/
│   └── (Next.js app — mic capture, WebSocket client, live transcript UI)
└── data/
    └── policy_corpus/           # raw scraped/downloaded policy docs, organized by jurisdiction
```

## Setup steps

1. `python -m venv .venv && source .venv/bin/activate`
2. Backend deps (`backend/requirements.txt`): `fastapi`, `uvicorn[standard]`, `websockets`,
   `langgraph`, `langchain-groq`, `groq`, `pinecone`, `supabase`, `edge-tts`, `python-dotenv`,
   `pydantic`, `httpx` (for Duffel — check if an official/community Python SDK is current and
   well-maintained before adding it; a thin `httpx` wrapper around the REST API is a safe
   fallback and gives full control over timeouts).
3. Frontend: `npx create-next-app@latest frontend`, add `ws` or native `WebSocket`, Tailwind.
4. Copy `.env.example` to `.env`, fill in real values. Fix the typo in
   `SUPABASE_DIRECT_CONNECTION_STRING` before using it anywhere (see `05_ENV_AND_SECRETS.md`).
5. Generate a Supabase secret key (`sb_secret_...`) — required for the webhook receiver and
   any server-side write that isn't scoped to a logged-in user. Add as `SUPABASE_SECRET_KEY`.
6. Run `backend/db/schema.sql` against the Supabase project to create the tables.

## What "initial prototype" means here — build a walking skeleton, not the full system

Do not build the LangGraph agent loop, Duffel integration, or validation gate first. Build the
**thinnest possible end-to-end path** and get it running locally before adding anything else:

1. Mic capture in the browser → send audio over WebSocket.
2. One STT call (transcribe only, skip the parallel translate call for now).
3. One hardcoded/stubbed response (skip retrieval entirely — return a fixed string).
4. One TTS call → stream audio back → play in browser.

Once that loop works end to end, layer in: real retrieval → real LLM call → the agent loop →
Duffel tools → the validation gate → real DB writes → barge-in handling. Update
`docs/agents/04_PROGRESS_AND_FEATURES.md` as each layer is added. Don't skip ahead — a broken
end-to-end skeleton is more useful to debug against than a polished module that isn't wired
into anything yet.
