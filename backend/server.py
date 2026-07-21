from __future__ import annotations

"""FastAPI server — WebSocket voice pipeline.

Phase 1.5: auth, conversation history, resume.
Phase 2:   parallel STT+translate, streaming sentence-level TTS, barge-in cancel.
"""

import asyncio
import json
import traceback
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Response, WebSocket, WebSocketDisconnect, UploadFile, File, Form
from .rag.ingest import ingest_policy_text
from .rag.url_pdf_parser import parse_url_to_markdown, parse_pdf_bytes_to_markdown
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .agent.graph import PRIMARY_MODEL, run_agent_turn
from .agent.tools.orders import OrderToolError, save_local_order
from .db.auth import AuthError, verify_supabase_access_token
from .db.conversations import (
    ConversationContext,
    ConversationHistoryError,
    get_conversation,
    create_conversation,
    record_turn,
)
from .db.memory import load_memory_context, update_memory_after_turn
from .db.supabase_client import get_service_supabase_client
from .voice.stt import SpeechToTextError, transcribe_audio
from .voice.tts import TextToSpeechError, stream_speech_sentences, synthesize_speech
from .voice.tts_format import format_text_for_tts
from .voice.router import route_voice_command, RouteResult
from .voice.voice_intents import (
    is_exit_phrase,
    pick_filler,
    pick_wait_phrase,
    voice_mode_end_ack,
)
from .webhooks.duffel_webhook import router as duffel_webhook_router

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

app = FastAPI(title="Airline Dispute Voice Prototype")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(duffel_webhook_router)


class LocalOrderCreate(BaseModel):
    order_type: str = Field(default="manual")
    status: str = Field(default="draft")
    duffel_order_id: str | None = None
    booking_reference: str | None = None
    airline: str | None = None
    origin: str | None = None
    destination: str | None = None
    departure_date: str | None = None
    amount: float | None = None
    fare_class: str | None = None
    raw_payload: dict[str, Any] | None = None


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/voice/tts/test/urdu")
async def test_urdu_tts() -> Response:
    text = "السلام علیکم۔ یہ اردو آواز کا ٹیسٹ ہے۔"
    try:
        audio = await synthesize_speech(text, "ur")
    except TextToSpeechError as exc:
        return Response(str(exc), status_code=502, media_type="text/plain")

    return Response(
        audio,
        media_type="audio/mpeg",
        headers={"Content-Disposition": 'inline; filename="urdu-tts-test.mp3"'},
    )


@app.delete("/conversations/{conversation_id}")
async def delete_conversation(
    conversation_id: str,
    authorization: str | None = Header(default=None),
) -> dict[str, str]:
    token = _bearer_token(authorization)
    try:
        user = await verify_supabase_access_token(token)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    client = get_service_supabase_client()
    existing = (
        client.table("conversations")
        .select("id")
        .eq("id", conversation_id)
        .eq("user_id", user.id)
        .limit(1)
        .execute()
    )
    if not existing.data:
        raise HTTPException(status_code=404, detail="Conversation not found.")

    client.table("conversations").delete().eq("id", conversation_id).execute()
    return {"status": "deleted"}


@app.get("/admin/debug")
async def admin_debug_info(
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Admin-only endpoint. Returns all users, memories, and conversations.
    Role is checked server-side against the DB — clients cannot self-elevate.
    """
    token = _bearer_token(authorization)
    try:
        user = await verify_supabase_access_token(token)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    svc = get_service_supabase_client()
    profile = svc.table("users").select("role").eq("id", user.id).limit(1).execute()
    if not profile.data or profile.data[0].get("role") != "admin":
        raise HTTPException(status_code=403, detail="Forbidden: admin access only.")

    users = svc.table("users").select("id,full_name,role,preferred_language").execute().data or []
    memories = svc.table("user_memories").select("*").order("last_seen_at", desc=True).limit(100).execute().data or []
    conversations = svc.table("conversations").select("id,user_id,title,status,primary_language,last_message_at").order("last_message_at", desc=True).limit(50).execute().data or []
    return {"users": users, "memories": memories, "conversations": conversations}


@app.get("/admin/debug/me")
async def admin_debug_current_user(
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Admin-only endpoint scoped to the signed-in user."""
    token = _bearer_token(authorization)
    try:
        user = await verify_supabase_access_token(token)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    svc = get_service_supabase_client()
    profile = svc.table("users").select("role").eq("id", user.id).limit(1).execute()
    if not profile.data or profile.data[0].get("role") != "admin":
        raise HTTPException(status_code=403, detail="Forbidden: admin access only.")

    user_row = (
        svc.table("users")
        .select("id,full_name,role,preferred_language")
        .eq("id", user.id)
        .limit(1)
        .execute()
        .data
        or []
    )
    memories = (
        svc.table("user_memories")
        .select("*")
        .eq("user_id", user.id)
        .order("last_seen_at", desc=True)
        .limit(20)
        .execute()
        .data
        or []
    )
    conversations = (
        svc.table("conversations")
        .select("id,user_id,title,status,primary_language,last_message_at")
        .eq("user_id", user.id)
        .order("last_message_at", desc=True)
        .limit(20)
        .execute()
        .data
        or []
    )
    return {
        "user": {
            **(user_row[0] if user_row else {"id": user.id}),
            "email": user.email,
        },
        "memories": memories,
        "conversations": conversations,
    }


@app.post("/orders/local")
async def create_local_order(
    order: LocalOrderCreate,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    token = _bearer_token(authorization)
    try:
        user = await verify_supabase_access_token(token)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    try:
        saved = await save_local_order(user_id=user.id, **order.model_dump())
    except OrderToolError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "created", "order": saved}


@app.websocket("/ws/voice")
async def voice_websocket(websocket: WebSocket) -> None:
    await websocket.accept()

    audio_buffer = bytearray()
    mime_type = "audio/webm"
    language_hint: str | None = None

    tts_cancel_event = asyncio.Event()
    current_task: asyncio.Task[None] | None = None

    try:
        # ── Phase 1.5: auth + conversation resume ──────────────────────────
        try:
            auth_event = await _receive_auth_event(websocket)
            user = await verify_supabase_access_token(auth_event.get("accessToken"))
            cid = auth_event.get("conversationId")
            if cid:
                conversation = await get_conversation(user_id=user.id, conversation_id=cid)
            else:
                conversation = None
        except (AuthError, ConversationHistoryError) as exc:
            await websocket.send_json({"type": "auth_required", "message": str(exc)})
            await websocket.close(code=1008)
            return

        await websocket.send_json(
            {
                "type": "ready",
                "userId": user.id,
                "conversationId": conversation.id if conversation else None,
            }
        )

        class SessionContext:
            def __init__(self, conv, u, admin: bool = False):
                self.conversation = conv
                self.user = u
                self.is_admin = admin
                self.last_language = "ur"

        # Check admin role via service client
        is_admin = False
        try:
            svc = get_service_supabase_client()
            profile = svc.table("users").select("role").eq("id", user.id).limit(1).execute()
            if profile.data and profile.data[0].get("role") == "admin":
                is_admin = True
        except Exception:
            pass

        session = SessionContext(conversation, user, admin=is_admin)

        while True:
            message = await websocket.receive()
            if current_task is not None and current_task.done():
                _consume_task_result(current_task)
                current_task = None

            if message.get("type") == "websocket.disconnect":
                if current_task is not None:
                    current_task.cancel()
                return

            if message.get("bytes") is not None:
                audio_buffer.extend(message["bytes"])
                continue

            if message.get("text") is None:
                continue

            event = _parse_event(message["text"])
            event_type = event.get("type")

            if event_type == "start":
                audio_buffer = bytearray()
                mime_type = event.get("mimeType") or "audio/webm"
                language_hint = _language_hint_from_event(event)
                tts_cancel_event.clear()
                await websocket.send_json({"type": "recording_started"})
                continue

            if event_type == "stop":
                if current_task is not None and not current_task.done():
                    wait_language = session.last_language or language_hint or "ur"
                    await _send_spoken_notice(
                        websocket,
                        pick_wait_phrase(wait_language),
                        wait_language,
                        tts_cancel_event,
                    )
                    audio_buffer = bytearray()
                    continue
                tts_cancel_event.clear()
                # Debug: log audio buffer size and mime type
                print(f"DEBUG: Audio buffer size: {len(audio_buffer)} bytes, MIME type: {mime_type}")
                if len(audio_buffer) == 0:
                    await websocket.send_json({"type": "error", "message": "No audio data received"})
                    continue
                current_task = asyncio.create_task(
                    _handle_turn(
                        websocket,
                        bytes(audio_buffer),
                        mime_type,
                        session,
                        tts_cancel_event,
                        language_hint,
                        is_admin=session.is_admin,
                    )
                )
                audio_buffer = bytearray()
                continue

            if event_type == "text_message":
                text = (event.get("text") or "").strip()
                if not text:
                    await websocket.send_json(
                        {"type": "error", "message": "Text message was empty."}
                    )
                    continue
                if current_task is not None and not current_task.done():
                    # Silently ignore - frontend handles buffering and will re-send when turn is complete
                    continue
                tts_cancel_event.clear()
                current_task = asyncio.create_task(
                    _handle_text_turn(
                        websocket,
                        text,
                        session,
                        _language_hint_from_event(event),
                        is_admin=session.is_admin,
                    )
                )
                continue

            if event_type == "cancel":
                tts_cancel_event.set()
                if current_task is not None and not current_task.done():
                    current_task.cancel()
                    current_task = None
                await websocket.send_json({"type": "cancelled"})
                continue

            if event_type == "speech_during_processing":
                wait_language = session.last_language or language_hint or "ur"
                await _send_spoken_notice(
                    websocket,
                    pick_wait_phrase(wait_language),
                    wait_language,
                    tts_cancel_event,
                )
                continue

            if event_type == "ping":
                await websocket.send_json({"type": "pong"})
                continue

            await websocket.send_json(
                {"type": "error", "message": f"Unknown event type: {event_type}"}
            )
    except WebSocketDisconnect:
        if current_task is not None:
            current_task.cancel()
        return


def _consume_task_result(task: asyncio.Task[None]) -> None:
    try:
        task.result()
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        import traceback
        print("Task failed with exception:")
        traceback.print_exc()


async def _receive_auth_event(websocket: WebSocket) -> dict[str, Any]:
    message = await websocket.receive()

    if message.get("type") == "websocket.disconnect":
        raise AuthError("WebSocket disconnected before login.")

    if message.get("text") is None:
        raise AuthError("Login is required before sending audio.")

    event = _parse_event(message["text"])
    if event.get("type") != "auth":
        raise AuthError("The first WebSocket event must authenticate the user.")

    return event


async def _handle_text_turn(
    websocket: WebSocket,
    text: str,
    session: Any,
    language_hint: str | None = None,
    is_admin: bool = False,
) -> None:
    try:
        if session.conversation is None:
            session.conversation = await create_conversation(user_id=session.user.id, title=text[:80])
            await websocket.send_json({"type": "conversation_created", "conversationId": session.conversation.id})
        conversation = session.conversation
        language = language_hint or _detect_supported_text_language(text)
        english_text = text if language == "en" else await _translate_to_english(text)
        await websocket.send_json({"type": "processing", "stage": "agent"})
        await websocket.send_json(
            {
                "type": "transcript",
                "text": text,
                "englishText": english_text,
                "language": language,
                "detectedLanguage": language,
            }
        )

        memory = await load_memory_context(conversation)

        async def token_callback(delta: str):
            if delta:
                await websocket.send_json({"type": "agent_token", "text": delta})

        async def debug_callback(trace_entry: dict):
            event_type = trace_entry.get("type")
            if event_type == "planning_complete":
                await websocket.send_json({
                    "type": "planning_complete",
                    "tools": trace_entry.get("planned_tools", []),
                    "category": trace_entry.get("category"),
                })
            elif event_type == "tool_start":
                await websocket.send_json({
                    "type": "tool_start",
                    "tool": trace_entry.get("tool"),
                })
            elif event_type == "tool_complete":
                await websocket.send_json({
                    "type": "tool_complete",
                    "tool": trace_entry.get("tool"),
                    "result": trace_entry.get("result_summary"),
                })
            elif event_type == "generation_start":
                await websocket.send_json({
                    "type": "generation_start",
                    "chunks_count": trace_entry.get("retrieved_chunks_count"),
                })
            elif is_admin:
                await websocket.send_json({"type": "debug_trace", "entry": trace_entry})

        async def status_callback(status: str):
            await websocket.send_json({"type": "processing", "stage": status})

        agent_result = await run_agent_turn(
            text,
            language,
            user_id=conversation.user_id,
            memory_context=memory.for_prompt(),
            token_callback=token_callback,
            debug_callback=debug_callback if is_admin else None,
            status_callback=status_callback,
        )

        await _send_admin_debug_bundle(
            websocket,
            query=text,
            language=language,
            agent_result=agent_result,
            memory=memory,
            is_admin=is_admin,
        )

        await websocket.send_json(
            {
                "type": "agent_response",
                "text": agent_result.response_text,
                "language": agent_result.language,
                "citations": _citation_payload(agent_result.retrieved_chunks),
            }
        )

        try:
            await record_turn(
                conversation=conversation,
                user_text=text,
                user_language=language,
                user_english_text=english_text,
                agent_text=agent_result.response_text,
                agent_language=agent_result.language,
            )
        except ConversationHistoryError as exc:
            await websocket.send_json({"type": "error", "message": str(exc)})
            return

        await update_memory_after_turn(
            conversation=conversation,
            user_text=text,
            user_language=language,
            agent_text=agent_result.response_text,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        traceback.print_exc()
        await websocket.send_json(
            {"type": "error", "message": f"Turn failed: {exc}"}
        )
    finally:
        await websocket.send_json({"type": "turn_complete"})


def _extract_internal_reasoning(debug_trace: list[dict]) -> str:
    for entry in reversed(debug_trace):
        if entry.get("node") == "reason" and entry.get("reasoning"):
            return str(entry["reasoning"])
        if entry.get("internal_reasoning"):
            return str(entry["internal_reasoning"])
    return ""


async def _send_admin_debug_bundle(
    websocket: WebSocket,
    *,
    query: str,
    language: str,
    agent_result: Any,
    memory: Any,
    is_admin: bool,
) -> None:
    if not is_admin:
        return
    await websocket.send_json(
        {
            "type": "debug_bundle",
            "payload": {
                "query": query,
                "language": language,
                "reasoning_chain": agent_result.debug_trace,
                "internal_reasoning": _extract_internal_reasoning(agent_result.debug_trace),
                "memory": memory.for_prompt(),
                "citations": _citation_payload(agent_result.retrieved_chunks),
                "cited_chunk_ids": agent_result.cited_chunk_ids,
                "response_preview": agent_result.response_text[:500],
            },
        }
    )


async def _send_spoken_notice(
    websocket: WebSocket,
    text: str,
    language: str,
    cancel_event: asyncio.Event,
) -> None:
    if cancel_event.is_set() or not text.strip():
        return
    try:
        audio = await synthesize_speech(text, language)
    except TextToSpeechError:
        return
    if cancel_event.is_set():
        return
    await websocket.send_json(
        {
            "type": "tts_audio",
            "mimeType": "audio/mpeg",
            "bytes": len(audio),
            "purpose": "notice",
        }
    )
    await websocket.send_bytes(audio)


async def _handle_turn(
    websocket: WebSocket,
    audio_bytes: bytes,
    mime_type: str,
    session: Any,
    cancel_event: asyncio.Event,
    language_hint: str | None = None,
    is_admin: bool = False,
) -> None:
    try:
        if not audio_bytes:
            await websocket.send_json(
                {"type": "error", "message": "No audio was received for this turn."}
            )
            return

        await websocket.send_json({"type": "processing", "stage": "stt"})

        try:
            stt_result = await transcribe_audio(
                audio_bytes,
                filename=f"claim.{_extension_for_mime_type(mime_type)}",
                content_type=mime_type,
                language_hint=language_hint,
            )
        except SpeechToTextError as exc:
            exc_str = str(exc)
            print(f"STT skipped: {exc_str}")
            is_silence = "No clear speech detected" in exc_str or "empty transcript" in exc_str
            await websocket.send_json(
                {
                    "type": "error",
                    "message": (
                        "Didn't catch that — please speak clearly and try again."
                        if is_silence
                        else "I could not understand that audio. Please try speaking again."
                    ),
                }
            )
            return

        english_text = stt_result.english_text or (
            stt_result.text if stt_result.language == "en" else None
        )

        session.last_language = stt_result.language

        await websocket.send_json(
            {
                "type": "transcript",
                "text": stt_result.text,
                "englishText": english_text,
                "language": stt_result.language,
                "detectedLanguage": stt_result.detected_language,
            }
        )

        if is_exit_phrase(stt_result.text, stt_result.language):
            ack = voice_mode_end_ack(stt_result.language)
            await websocket.send_json({"type": "voice_mode_end"})
            await _send_spoken_notice(
                websocket,
                ack,
                stt_result.language,
                cancel_event,
            )
            return

        # Voice Router - handle simple commands without invoking agent
        route_result = route_voice_command(stt_result.text, stt_result.language)

        if route_result.category != "agent":
            # Handle routed command immediately
            response_text = (
                route_result.response_en
                if stt_result.language == "en"
                else route_result.response_ur
            )

            # Send the response
            await websocket.send_json(
                {
                    "type": "agent_response",
                    "text": response_text,
                    "language": stt_result.language,
                    "citations": [],
                }
            )

            # TTS for the response
            try:
                audio = await synthesize_speech(response_text, stt_result.language)
                await websocket.send_json({"type": "tts_audio", "language": stt_result.language})
                await websocket.send_bytes(audio)
            except TextToSpeechError:
                pass  # TTS failure is not critical for simple commands

            # Record the turn if we have a conversation
            if session.conversation:
                try:
                    await record_turn(
                        conversation=session.conversation,
                        user_text=stt_result.text,
                        user_language=stt_result.language,
                        user_english_text=english_text,
                        agent_text=response_text,
                        agent_language=stt_result.language,
                    )
                except ConversationHistoryError:
                    pass  # Recording failure is not critical

            # Handle interrupt if needed
            if route_result.should_interrupt:
                await websocket.send_json({"type": "cancelled"})

            return

        await websocket.send_json({"type": "processing", "stage": "agent"})
        await _send_spoken_notice(
            websocket,
            pick_filler(stt_result.language),
            stt_result.language,
            cancel_event,
        )
        if cancel_event.is_set():
            return

        agent_query = english_text or stt_result.text

        if session.conversation is None:
            session.conversation = await create_conversation(user_id=session.user.id, title=stt_result.text[:80])
            await websocket.send_json({"type": "conversation_created", "conversationId": session.conversation.id})
        conversation = session.conversation

        memory = await load_memory_context(conversation)
        send_lock = asyncio.Lock()
        tts_tasks: list[asyncio.Task[bytes | None]] = []
        tts_queue = asyncio.Queue()
        tts_stage_sent = False

        async def tts_worker():
            while True:
                task = await tts_queue.get()
                if task is None:
                    break
                try:
                    audio = await task
                    if audio and not cancel_event.is_set():
                        async with send_lock:
                            await websocket.send_json(
                                {
                                    "type": "tts_audio",
                                    "mimeType": "audio/mpeg",
                                    "bytes": len(audio),
                                    "purpose": "response",
                                }
                            )
                            await websocket.send_bytes(audio)
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    print(f"TTS error: {e}")
                finally:
                    tts_queue.task_done()
                    
        worker_task = asyncio.create_task(tts_worker())

        async def token_callback(delta: str):
            if delta and not cancel_event.is_set():
                async with send_lock:
                    await websocket.send_json({"type": "agent_token", "text": delta})

        async def sentence_callback(sentence: str):
            nonlocal tts_stage_sent
            if cancel_event.is_set() or not sentence.strip():
                return
            spoken_sentence = format_text_for_tts(sentence, stt_result.language)
            if not spoken_sentence.strip():
                return
            if not tts_stage_sent:
                tts_stage_sent = True
                async with send_lock:
                    await websocket.send_json({"type": "processing", "stage": "tts"})

            async def synthesize_sentence() -> bytes | None:
                try:
                    return await synthesize_speech(spoken_sentence, stt_result.language)
                except TextToSpeechError:
                    return None

            task = asyncio.create_task(synthesize_sentence())
            tts_tasks.append(task)
            await tts_queue.put(task)

        async def debug_callback(trace_entry: dict):
            if not cancel_event.is_set():
                event_type = trace_entry.get("type")
                if event_type == "planning_complete":
                    await websocket.send_json({
                        "type": "planning_complete",
                        "tools": trace_entry.get("planned_tools", []),
                        "category": trace_entry.get("category"),
                    })
                elif event_type == "tool_start":
                    await websocket.send_json({
                        "type": "tool_start",
                        "tool": trace_entry.get("tool"),
                    })
                elif event_type == "tool_complete":
                    await websocket.send_json({
                        "type": "tool_complete",
                        "tool": trace_entry.get("tool"),
                        "result": trace_entry.get("result_summary"),
                    })
                elif event_type == "generation_start":
                    await websocket.send_json({
                        "type": "generation_start",
                        "chunks_count": trace_entry.get("retrieved_chunks_count"),
                    })
                elif is_admin:
                    await websocket.send_json({"type": "debug_trace", "entry": trace_entry})

        async def status_callback(status: str):
            if not cancel_event.is_set():
                async with send_lock:
                    await websocket.send_json({"type": "processing", "stage": status})

        agent_result = await run_agent_turn(
            agent_query,
            stt_result.language,
            user_id=conversation.user_id,
            memory_context=memory.for_prompt(),
            token_callback=token_callback,
            sentence_callback=sentence_callback,
            debug_callback=debug_callback if is_admin else None,
            status_callback=status_callback,
            for_voice=True,
        )

        await _send_admin_debug_bundle(
            websocket,
            query=agent_query,
            language=stt_result.language,
            agent_result=agent_result,
            memory=memory,
            is_admin=is_admin,
        )

        response_text = agent_result.response_text
        response_language = agent_result.language

        async with send_lock:
            await websocket.send_json(
                {
                    "type": "agent_response",
                    "text": response_text,
                    "language": response_language,
                    "citations": _citation_payload(agent_result.retrieved_chunks),
                }
            )

        try:
            await record_turn(
                conversation=conversation,
                user_text=stt_result.text,
                user_language=stt_result.language,
                user_english_text=english_text,
                agent_text=response_text,
                agent_language=response_language,
            )
        except ConversationHistoryError as exc:
            await websocket.send_json({"type": "error", "message": str(exc)})
            return

        await update_memory_after_turn(
            conversation=conversation,
            user_text=stt_result.text,
            user_language=stt_result.language,
            agent_text=response_text,
        )

        if tts_tasks:
            await tts_queue.put(None)
            await worker_task

        if not tts_tasks:
            await websocket.send_json({"type": "processing", "stage": "tts"})
            spoken_response = format_text_for_tts(response_text, response_language)
            try:
                async for sentence_audio in stream_speech_sentences(
                    spoken_response,
                    response_language,
                    cancel_event=cancel_event,
                ):
                    if cancel_event.is_set():
                        break
                    await websocket.send_json(
                        {
                            "type": "tts_audio",
                            "mimeType": "audio/mpeg",
                            "bytes": len(sentence_audio),
                            "purpose": "response",
                        }
                    )
                    await websocket.send_bytes(sentence_audio)
            except TextToSpeechError as exc:
                # Send a simple error message without calling LLM
                await websocket.send_json({
                    "type": "tts_error",
                    "message_en": "No voice was detected please try again",
                    "message_ur": "آواز نہیں ملی، براہ کرم دوبارہ کوشش کریں"
                })
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        traceback.print_exc()
        await websocket.send_json(
            {"type": "error", "message": f"Turn failed: {exc}"}
        )
    finally:
        await websocket.send_json({"type": "turn_complete"})


def _parse_event(raw_event: str) -> dict[str, Any]:
    try:
        event = json.loads(raw_event)
    except json.JSONDecodeError:
        return {"type": "invalid_json"}
    return event if isinstance(event, dict) else {"type": "invalid_json"}


def _bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return token


def _citation_payload(chunks: list[dict]) -> list[dict]:
    return [
        {
            "id": chunk.get("id"),
            "title": chunk.get("title"),
            "heading": chunk.get("heading"),
            "jurisdiction": chunk.get("jurisdiction"),
            "score": chunk.get("score"),
            "originalText": chunk.get("chunk_text"),
            "sourceUrl": chunk.get("source_url"),
        }
        for chunk in chunks[:5]
    ]


def _detect_supported_text_language(text: str) -> str:
    if any("\u0600" <= char <= "\u06ff" for char in text):
        return "ur"
    if any("\u0900" <= char <= "\u097f" for char in text):
        return "ur"
    letters = [char for char in text if char.isalpha()]
    if letters and all(ord(char) < 128 for char in letters):
        return "en"
    if any(ord(char) > 127 for char in text):
        return "ur"
    return "en"


async def _translate_to_english(text: str) -> str | None:
    import os

    from groq import AsyncGroq

    api_key = os.getenv("GROQ_API_KEY")
    if not api_key or not text.strip():
        return None
    try:
        client = AsyncGroq(api_key=api_key)
        completion = await client.chat.completions.create(
            model=PRIMARY_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": "Translate the user message to English. Output only the translation.",
                },
                {"role": "user", "content": text},
            ],
            temperature=0.0,
            max_tokens=200,
        )
        translated = (completion.choices[0].message.content or "").strip()
        return translated or None
    except Exception:
        return None


def _language_hint_from_event(event: dict[str, Any]) -> str | None:
    mode = str(event.get("languageMode") or "auto").lower()
    if mode in {"en", "english"}:
        return "en"
    if mode in {"ur", "urdu"}:
        return "ur"
    return None


def _extension_for_mime_type(mime_type: str) -> str:
    if "mp4" in mime_type:
        return "mp4"
    if "mpeg" in mime_type or "mp3" in mime_type:
        return "mp3"
    if "ogg" in mime_type:
        return "ogg"
    if "wav" in mime_type:
        return "wav"
    return "webm"


@app.post("/admin/ingest/preview")
async def admin_ingest_preview(
    authorization: str | None = Header(default=None),
    source_type: str = Form(...),
    url: str | None = Form(default=None),
    file: UploadFile | None = File(default=None),
    text: str | None = Form(default=None),
    source_name: str | None = Form(default=None),
    category: str | None = Form(default=None),
    jurisdiction: str | None = Form(default=None),
    carrier: str | None = Form(default=None),
    regulator: str | None = Form(default=None),
) -> dict[str, Any]:
    token = _bearer_token(authorization)
    try:
        user = await verify_supabase_access_token(token)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    svc = get_service_supabase_client()
    profile = svc.table("users").select("role").eq("id", user.id).limit(1).execute()
    if not profile.data or profile.data[0].get("role") != "admin":
        raise HTTPException(status_code=403, detail="Forbidden: admin access only.")

    try:
        if source_type == "url":
            if not url:
                raise HTTPException(status_code=400, detail="url is required for source_type='url'")
            markdown_content = await parse_url_to_markdown(url)
            name = source_name or url.split("/")[-1] or url
        elif source_type == "pdf":
            if not file:
                raise HTTPException(status_code=400, detail="file is required for source_type='pdf'")
            content_bytes = await file.read()
            markdown_content = parse_pdf_bytes_to_markdown(content_bytes)
            name = source_name or file.filename or "uploaded_pdf"
        elif source_type == "text":
            if not text:
                raise HTTPException(status_code=400, detail="text is required for source_type='text'")
            markdown_content = text
            name = source_name or "raw_text_input"
        else:
            raise HTTPException(status_code=400, detail="Invalid source_type. Must be url, pdf, or text.")
            
        frontmatter_lines = []
        if category: frontmatter_lines.append(f"category: {category}")
        if jurisdiction: frontmatter_lines.append(f"jurisdiction: {jurisdiction}")
        if carrier: frontmatter_lines.append(f"carrier: {carrier}")
        if regulator: frontmatter_lines.append(f"regulator: {regulator}")
        
        if frontmatter_lines:
            # If the markdown already has frontmatter, this prepended block will be parsed first.
            fm = "---\n" + "\n".join(frontmatter_lines) + "\n---\n\n"
            markdown_content = fm + markdown_content

        from backend.rag.chunker import chunk_policy_text
        doc, chunks = chunk_policy_text(markdown_content, source_name=name)
        
        return {
            "status": "success",
            "markdown": markdown_content,
            "document": {
                "title": doc.title,
                "version": doc.version,
                "category": doc.category,
                "jurisdiction": doc.jurisdiction,
            },
            "chunks_count": len(chunks),
            "chunks": [
                {
                    "index": c.chunk_index,
                    "heading": c.heading,
                    "text": c.chunk_text,
                }
                for c in chunks
            ],
            "name": name,
        }
    except Exception as exc:
        print(f"[Ingest] ERROR: {exc}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Preview failed: {exc}")

@app.post("/admin/ingest/confirm")
async def admin_ingest_confirm(
    authorization: str | None = Header(default=None),
    text: str = Form(...),
    source_name: str = Form(...),
) -> dict[str, Any]:
    token = _bearer_token(authorization)
    try:
        user = await verify_supabase_access_token(token)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    svc = get_service_supabase_client()
    profile = svc.table("users").select("role").eq("id", user.id).limit(1).execute()
    if not profile.data or profile.data[0].get("role") != "admin":
        raise HTTPException(status_code=403, detail="Forbidden: admin access only.")

    try:
        print(f"[Ingest] Confirmed processing for source: {source_name}")
        result = ingest_policy_text(
            raw_text=text,
            source_name=source_name
        )
        print(f"[Ingest] Successfully ingested {result['chunks']} chunks for {source_name}")
        return {
            "status": "success",
            "chunks_ingested": result["chunks"]
        }
    except Exception as exc:
        print(f"[Ingest] ERROR: {exc}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Ingestion failed: {exc}")
