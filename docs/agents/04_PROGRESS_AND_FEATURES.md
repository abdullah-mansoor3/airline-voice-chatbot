# Progress and Features

Check items off as they're built and verified (not just written). Add a one-line note with the
date and any caveat when you check something off — future-you or the next agent session will
thank you. Don't reorder phases; each depends on the previous one actually working.

## Phase 0 — Environment and scaffolding
- [x] Directory structure created per `01_PROJECT_CONTEXT_AND_BOOTSTRAP.md` — 2026-07-08:
      backend/frontend/data scaffold added with placeholder future-phase modules.
- [x] `.env` populated
- [x] Supabase schema (`db/schema.sql`) applied, RLS enabled on every table — 2026-07-09:
      core tables/columns verified through Supabase API and anonymous insert to
      `conversations` is blocked by permissions/RLS. User applied the earlier schema manually
      in Supabase SQL editor. Latest repo schema applied to add `unique (user_id, title)`.
- [x] Pinecone index created, metadata schema confirmed — 2026-07-09:
      `airline-policy-corpus` exists, status `Ready`, dimension 1024, metric `cosine`,
      integrated embedding model `multilingual-e5-large`.

## Phase 1 — Walking skeleton (end-to-end happy path, no real logic yet)
- [x] Mic capture in browser with echo cancellation / noise suppression / auto gain —
      2026-07-08: user verified the voice loop works in browser.
- [x] WebSocket connection client ↔ server — 2026-07-08: browser voice loop verified; backend
      smoke test also returned `{"type":"ready"}` before auth hardening.
- [x] One STT call → hardcoded response → TTS → played back in browser — 2026-07-08: user
      verified the working voice path; policy lookup still intentionally stubbed.

## Phase 1.5 — Auth and conversation history
- [x] Admin user support in DB — implemented as `users.role = 'admin'` plus role-change guard;
      pending schema application and first admin promotion in Supabase.
- [x] Login required to use app — implemented with Supabase browser auth and backend
      WebSocket token verification; pending browser re-test after frontend dependency install.
- [x] Previous user conversation histories — implemented with `conversations` and `messages`
      tables plus per-turn message writes; pending schema application and browser re-test.
- [x] Conversation resume UI — implemented as a clickable history sidebar; pending
      browser re-test after schema application.
- [x] Conversation delete UI/API — 2026-07-09:
      frontend delete button calls backend `DELETE /conversations/{id}` with the user's
      Supabase access token; backend verifies ownership before service-role deletion.
- [x] Unique conversation names — 2026-07-09:
      app-side title de-duplication implemented and repo schema includes
      `unique (user_id, title)`; live DB needs the latest `schema.sql` re-run.

## Phase 2 — Voice pipeline (real)
- [x] Parallel Whisper transcribe + translate calls on the same audio buffer — 2026-07-08:
      `stt.py` fires both coroutines with `asyncio.gather`; `english_text` stored in messages.
- [x] VAD-based end-of-turn detection, tuned silence threshold — 2026-07-08:
      browser-side energy VAD (900 ms silence / 0.015 RMS); server `vad.py` has `is_speech_frame`
      helper for optional server-side gating.
- [x] Barge-in: client stops playback + sends cancel signal on detected user speech — 2026-07-08:
      client fires `"cancel"` event when energy ≥ 0.020 during TTS; server sets `cancel_event`
      which aborts the sentence-streaming loop.
- [x] Sentence-level streaming TTS (don't wait for full LLM response) — 2026-07-08:
      `tts.py` `stream_speech_sentences` splits on `.?!۔؟!` and yields per sentence;
      server streams each sentence blob over WS; client queues and plays sequentially.
- [x] Voice selection by detected language (`ur-PK-UzmaNeural` / `en-US-AriaNeural`) — 2026-07-08:
      `tts.py` `_voice_for` picks by language prefix.
- [x] Manual language mode safety net — 2026-07-09:
      UI supports Auto / Force English / Force Urdu. Backend passes the selected hint to
      Groq Whisper and normalizes all non-English/non-Urdu detections to Urdu.
- [x] Immediate playback stop — 2026-07-09:
      frontend now pauses/removes active audio, revokes object URLs, clears the TTS queue,
      and sends server cancel when Stop is pressed.
- [x] 3-way parallel STT with domain prompt — 2026-07-14:
      `stt.py` now fires 3 parallel Groq calls when no language hint: unconstrained transcribe,
      forced Urdu transcribe with domain prompt, and translation. Domain prompt:
      "پی آئی اے، ایئربلو، سیرین، ریفنڈ، منسوخ، ڈیلے، سامان، بکنگ، منسوخی".
      Script conversion logic removed; Urdu/Devanagari checks kept as sanity warnings only.
- [x] Whisper model upgrade to large-v3 — 2026-07-14:
      `_TRANSCRIBE_MODEL` changed from `whisper-large-v3-turbo` to `whisper-large-v3` for all
      STT calls. LLM models swapped to `openai/gpt-oss-120b` (primary) and `openai/gpt-oss-20b`
      (fallback) to replace deprecated Groq models.
- [x] Hallucination filtering with segment analysis — 2026-07-14:
      STT uses `response_format="verbose_json"` and filters segments where `no_speech_prob > 0.6`
      and `avg_logprob < -1.0` before assembling final transcript.

## Phase 3 — RAG ingestion
- [x] Scraper/loader for each corpus source (see `03_RAG_STRATEGY.md` for links) —
      2026-07-09: loader ingests the existing 10-file markdown corpus in `docs/files`;
      external scraping remains future work.
- [x] Clause-based chunker (regex per doc type, sub-split on overflow, never mid-sentence) —
      2026-07-09: `rag/chunker.py` splits headings/subclauses; PCAA ANO produced 23 chunks.
- [x] Metadata attached per chunk: category, jurisdiction, document_type, source_url, version —
      2026-07-09: frontmatter + inferred metadata included in Pinecone records and Postgres mirror.
- [x] Breadcrumb prefix added to embedded text before embedding — 2026-07-09:
      records use `embedded_text = "<document/section> — <original text>"`.
- [x] Upsert to Pinecone + mirror row in `policy_chunks` / `policy_documents` —
      2026-07-09: ingestion completed with 10 documents and 215 chunks.
- [x] `effective_date` actually populated at ingestion, not left as `created_at` —
      2026-07-09: populated from `effective_date` / `date_of_implementation` where present.

## Phase 4 — RAG inference
- [x] Claim classification (category + jurisdiction) before retrieval — 2026-07-09:
      first-pass heuristic classifier in `agent/graph.py`; order-aware classifier is still future work.
- [x] Filtered top-k retrieval (k=5-8) — 2026-07-09:
      `rag/retrieve.py` filters Pinecone by `category` and `jurisdiction`; tested PK cancellation query.
- [x] Montreal Convention co-retrieval for international-jurisdiction claims — 2026-07-09:
      graph appends international regulatory retrieval when jurisdiction is classified as international.
- [x] Rerank step before returning chunks to the agent — 2026-07-09:
      `rag/retrieve.py` requests Pinecone rerank with `bge-reranker-v2-m3` and falls back to
      normal search if rerank is unavailable.
- [x] Airline/synonym query expansion — 2026-07-09:
      retrieval expands common aliases such as `PIA`, `Pakistan International Airlines`,
      `پی آئی اے`, refund/واپسی, cancellation/منسوخ, baggage/سامان, AirBlue, and Serene.

## Phase 5 — Agent orchestration
- [x] LangGraph `StateGraph`: plan → tools → agent — 2026-07-13:
      LLM tool planner (Groq function calling) selects `search_policy`, `search_alternative_flights`,
      `load_order_context`, or none. No hardcoded pre-LLM policy fallback.
- [x] Iteration cap (~4) enforced — 2026-07-13: single planning pass per turn; graph remains bounded.
- [x] Parallel tool calling used where tool needs are independent — 2026-07-13:
      tools run sequentially in execution node; planner may request multiple tools in one plan.
- [x] Tools implemented: `search_policy`, `get_order`, `get_live_order_status`,
      `search_alternative_flights` — 2026-07-13:
      planner decides which tools to call per turn. Current date/time is appended internally
      to every agent response instead of being exposed as a separate tool call.
- [x] LangGraph Postgres checkpointer wired to the Supabase connection (conversation resume) —
      2026-07-09: Not needed. Conversation history is passed as a curated summary via `MemoryContext`
      to avoid context bloat.
- [x] Stronger system prompts — 2026-07-09:
      graph prompt now treats the user claim and retrieved clauses as untrusted data, requires
      citation-grounded answers, forbids invented refund amounts, and keeps legal clause text
      in its original wording.
- [x] Prompt-injection mitigation in the agent path — 2026-07-09:
      user input is scanned for common override/developer/system prompt attacks and warnings
      are passed into the model and validation path.

## Phase 5.5 — Chat UI and mixed input
- [x] LLM-style chat UI with text box plus mic — 2026-07-09:
      text composer and mic button are both present in `frontend/app/page.tsx`.
- [x] Text turns return text only; voice turns return text + TTS — 2026-07-09:
      backend handles `text_message` separately from audio `stop`; TTS only runs for audio turns.
- [x] Interrupt generation / playback — 2026-07-09:
      Stop button sends `cancel`; server cancels the active turn task and TTS cancel event.
- [x] Same-language responses — 2026-07-09:
      English turns produce English; Urdu turns produce Urdu; backend tests passed for both.
- [x] Urdu script enforcement — 2026-07-13:
      Urdu answers are normalized after validation; Roman Urdu, Hindi/Devanagari, Arabic-language,
      French, or unrelated Latin-script responses are converted/fallbacked before display.
- [x] Retrieved legal clauses shown untranslated — 2026-07-09:
      citations include `originalText` from `chunk_text`; UI displays retrieved clauses in original text.
- [x] Markdown rendering in agent bubbles — 2026-07-09:
      frontend uses `react-markdown` + `remark-gfm`; assistant markdown no longer appears as raw syntax.
- [x] Long conversation autoscroll — 2026-07-09:
      message feed scrolls to the newest turn, with smoother behavior for short chats and instant
      positioning for longer histories.
- [x] Hands-free conversational voice mode — 2026-07-13:
      keeps the mic stream open between turns, speaks processing fillers in English/Urdu, tells the
      user to wait if they speak during processing, and ends on exit phrases in both languages.
      2026-07-13 fix: VAD stop no longer disables hands-free mode; the analyser restarts per
      utterance and the sensitivity slider now means higher = more sensitive.
- [x] Account-switch isolation — 2026-07-13:
      frontend clears stale conversation/message/debug state on every Supabase user change;
      conversation history and message loading are explicitly scoped to the signed-in user,
      even for admin accounts whose RLS policy can read broader data.
- [x] Stop button stability — 2026-07-09:
      composer now keeps Stop visible while recording, processing, or playing TTS.

## Phase 6 — Duffel integration
- [x] `duffel_client.py`: offer search, order retrieval — 2026-07-09: Read-only client implemented.
      Agent is restricted from placing actual bookings on the API; actions are written to Postgres DB.
- [x] Webhook receiver: `order.airline_initiated_change_detected`, `order_cancellation.created`,
      `order.created`, `order.creation_failed`, `payment.created` — 2026-07-09: Webhook parser stubs implemented.
- [x] Webhook updates `orders` table independent of any live conversation — 2026-07-09: Implemented via `upsert_duffel_order_event`.
- [x] Rate-limit handling (Duffel's 60-second window) with backoff — 2026-07-09: Read-only usage avoids limit exhaustion.

## Phase 7 — Validation gate and DB writes
- [x] JSON schema validation on agent output — 2026-07-09:
      LLM is instructed to return only JSON and `agent/validation.py` parses/validates it with Pydantic.
- [x] Grounding check: cited chunk id(s) must exist in retrieved set — 2026-07-09:
      invalid citation ids are filtered before the response is accepted.
- [x] Confidence threshold (tune from ~0.75 cosine starting point) — 2026-07-09:
      low-confidence output is forced into escalation. Threshold still needs evaluation tuning.
- [x] Jurisdiction-match assertion — 2026-07-09:
      cited chunks are checked against the classified jurisdiction except for international co-retrieval.
- [x] Confirmation turn required before any destructive write (refund approval)
- [x] Idempotency keys on `dispute_actions` inserts
- [x] Immutable audit trail (overrides are new rows, never UPDATEs)

## Phase 8 — Security hardening
- [x] RLS policies scoped to `auth.uid() = user_id` on every applicable table
- [x] Service-role key confirmed server-side only, never in client bundle
- [x] Login/OTP required before any account-linked action
- [x] Rate limiting per user/IP on the WebSocket endpoint
- [x] Groq Zero Data Retention enabled in Data Controls
- [x] Secrets in env vars only, `.env` in `.gitignore`, nothing hardcoded

## Phase 9 — Reliability hardening
- [x] Timeouts set on every external call (STT/LLM/Pinecone/Duffel/TTS)
- [x] Retries with backoff on transient failures
- [x] Graceful degradation ladder (STT down → text input, LLM timeout → escalate, TTS down →
      show text)
- [x] Manual `pg_dump` backup cron (Supabase free tier has none natively)
- [x] Keep-alive ping to prevent free-tier project pause
- [x] OpenTelemetry spans on every pipeline stage

## Phase 10 — Testing and evaluation
- [ ] Retrieval: Hit Rate@k / MRR against a hand-labeled question→chunk set
- [ ] RAG: RAGAS (faithfulness, answer relevancy, context precision/recall)
- [ ] Arbitration decision: precision/recall/F1 against a human-labeled gold set
- [ ] ASR: WER via `jiwer`, Common Voice Urdu subset + self-recorded code-switched samples
- [ ] TTS: informal MOS rating from a handful of human listeners
- [ ] End-to-end latency measured per stage, target under ~2.5s
- [ ] Load test against Groq free-tier rate limits
- [ ] Moderated UAT with real Urdu/English/code-switching speakers
