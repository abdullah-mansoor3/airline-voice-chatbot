from __future__ import annotations

"""FastAPI server — WebSocket voice pipeline.

Phase 1.5: auth, conversation history, resume.
Phase 2:   parallel STT+translate, streaming sentence-level TTS, barge-in cancel.
"""

import asyncio
import json
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .agent.graph import run_agent_turn
from .agent.tools.orders import OrderToolError, save_local_order
from .db.auth import AuthError, verify_supabase_access_token
from .db.conversations import (
    ConversationContext,
    ConversationHistoryError,
    get_or_create_conversation,
    record_turn,
)
from .db.memory import load_memory_context, update_memory_after_turn
from .db.supabase_client import get_service_supabase_client
from .voice.stt import SpeechToTextError, transcribe_audio
from .voice.tts import TextToSpeechError, stream_speech_sentences, synthesize_speech
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
            conversation = await get_or_create_conversation(
                user_id=user.id,
                conversation_id=auth_event.get("conversationId"),
            )
        except (AuthError, ConversationHistoryError) as exc:
            await websocket.send_json({"type": "auth_required", "message": str(exc)})
            await websocket.close(code=1008)
            return

        await websocket.send_json(
            {
                "type": "ready",
                "userId": user.id,
                "conversationId": conversation.id,
            }
        )

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
                if current_task is not None:
                    await websocket.send_json(
                        {"type": "error", "message": "A turn is already processing."}
                    )
                    continue
                tts_cancel_event.clear()
                current_task = asyncio.create_task(
                    _handle_turn(
                        websocket,
                        bytes(audio_buffer),
                        mime_type,
                        conversation,
                        tts_cancel_event,
                        language_hint,
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
                if current_task is not None:
                    await websocket.send_json(
                        {"type": "error", "message": "A turn is already processing."}
                    )
                    continue
                tts_cancel_event.clear()
                current_task = asyncio.create_task(
                    _handle_text_turn(
                        websocket,
                        text,
                        conversation,
                        _language_hint_from_event(event),
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
        return
    except Exception:
        return


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
    conversation: ConversationContext,
    language_hint: str | None = None,
) -> None:
    language = language_hint or _detect_supported_text_language(text)
    await websocket.send_json({"type": "processing", "stage": "agent"})
    await websocket.send_json(
        {
            "type": "transcript",
            "text": text,
            "englishText": text if language == "en" else None,
            "language": language,
            "detectedLanguage": language,
        }
    )

    memory = await load_memory_context(conversation)
    agent_result = await run_agent_turn(
        text,
        language,
        user_id=conversation.user_id,
        memory_context=memory.for_prompt(),
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
            user_english_text=text if language == "en" else None,
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

    await websocket.send_json({"type": "turn_complete"})


async def _handle_turn(
    websocket: WebSocket,
    audio_bytes: bytes,
    mime_type: str,
    conversation: ConversationContext,
    cancel_event: asyncio.Event,
    language_hint: str | None = None,
) -> None:
    if not audio_bytes:
        await websocket.send_json(
            {"type": "error", "message": "No audio was received for this turn."}
        )
        return

    await websocket.send_json({"type": "processing", "stage": "stt"})

    try:
        # Phase 2: parallel transcribe + translate.
        stt_result = await transcribe_audio(
            audio_bytes,
            filename=f"claim.{_extension_for_mime_type(mime_type)}",
            content_type=mime_type,
            language_hint=language_hint,
        )
    except SpeechToTextError as exc:
        await websocket.send_json({"type": "error", "message": str(exc)})
        return

    english_text = stt_result.english_text or (
        stt_result.text if stt_result.language == "en" else None
    )

    await websocket.send_json(
        {
            "type": "transcript",
            "text": stt_result.text,
            "englishText": english_text,
            "language": stt_result.language,
            "detectedLanguage": stt_result.detected_language,
        }
    )

    await websocket.send_json({"type": "processing", "stage": "agent"})
    agent_query = english_text or stt_result.text
    memory = await load_memory_context(conversation)
    agent_result = await run_agent_turn(
        agent_query,
        stt_result.language,
        user_id=conversation.user_id,
        memory_context=memory.for_prompt(),
    )
    response_text = agent_result.response_text
    response_language = agent_result.language

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

    await websocket.send_json({"type": "processing", "stage": "tts"})

    # Phase 2: sentence-level streaming TTS with barge-in cancel support.
    try:
        async for sentence_audio in stream_speech_sentences(
            response_text,
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
                }
            )
            await websocket.send_bytes(sentence_audio)
    except TextToSpeechError as exc:
        await websocket.send_json({"type": "error", "message": str(exc)})
        return

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
    if all(ord(char) < 128 for char in text):
        return "en"
    return "ur"


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
