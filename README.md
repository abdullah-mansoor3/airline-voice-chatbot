# Airline Dispute Voice Agent

Bilingual Urdu/English voice prototype for airline dispute intake. The current code is the
Phase 1 walking skeleton: browser mic capture sends audio to a FastAPI WebSocket, the backend
transcribes one turn with Groq Whisper, returns a fixed response, synthesizes it with
`edge-tts`, and sends the MP3 back to the browser.

The architecture notes live in `docs/agents/`. Read these before expanding the prototype:

- `02_SYSTEM_LOOKUP.md` for module ownership, turn flow, and DB schema
- `03_RAG_STRATEGY.md` before touching ingestion or retrieval
- `06_DECISIONS_LOG.md` before changing architectural choices

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r backend/requirements.txt
cp .env.example .env
```

Fill `.env` with real values. The walking skeleton requires `GROQ_API_KEY` for STT; TTS uses
`edge-tts` and does not require a key.

Install frontend dependencies when Node.js is available:

```bash
npm --prefix frontend install
```

## Run Locally

Backend:

```bash
source .venv/bin/activate
python3 -m uvicorn backend.server:app --host 0.0.0.0 --port 8000 --reload
```

Frontend:

```bash
npm --prefix frontend run dev
```

Then open `http://localhost:3000` for the landing page, `http://localhost:3000/login` to sign in,
and `http://localhost:3000/chat` for the assistant.

## Duffel flight search

If flight search returns `insufficient_permissions` / 403, your Duffel API token is missing the
`air.offer_requests.create` scope. In the [Duffel dashboard](https://app.duffel.com/api-keys),
create or edit a token and enable **Offer requests → Create**. Update `DUFFEL_API_KEY` in `.env`
and restart the backend.

## WebSocket Protocol

Client to server:

- `{"type":"start","mimeType":"audio/webm;codecs=opus"}`
- binary audio chunks
- `{"type":"stop"}`

Server to client:

- status events (`ready`, `processing`, `turn_complete`)
- transcript and fixed response JSON
- one binary `audio/mpeg` payload for playback

## Phase Boundary

Do not add retrieval, LangGraph, Duffel tools, validation, or DB writes until the local voice
loop has been verified end to end. The placeholder modules exist only to preserve the agreed
directory map.
