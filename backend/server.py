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
from fastapi import FastAPI, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from .db.auth import AuthError, verify_supabase_access_token
from .db.conversations import (
    ConversationContext,
    ConversationHistoryError,
    get_or_create_conversation,
    record_turn,
)
from .voice.stt import SpeechToTextError, transcribe_audio
from .voice.tts import TextToSpeechError, stream_speech_sentences, synthesize_speech

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

app = FastAPI(title="Airline Dispute Voice Prototype")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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


@app.websocket("/ws/voice")
async def voice_websocket(websocket: WebSocket) -> None:
    await websocket.accept()

    audio_buffer = bytearray()
    mime_type = "audio/webm"

    # Phase 2: cancel event for barge-in — set when the client sends "cancel".
    tts_cancel_event = asyncio.Event()
    # Track whether TTS is currently streaming so we know whether to ack cancels.
    tts_active = False

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

            if message.get("type") == "websocket.disconnect":
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
                tts_cancel_event.clear()
                await websocket.send_json({"type": "recording_started"})
                continue

            if event_type == "stop":
                tts_cancel_event.clear()
                tts_active = False
                await _handle_turn(
                    websocket,
                    bytes(audio_buffer),
                    mime_type,
                    conversation,
                    tts_cancel_event,
                )
                audio_buffer = bytearray()
                tts_active = False
                continue

            # Phase 2: barge-in cancel — client detected speech during TTS playback.
            if event_type == "cancel":
                tts_cancel_event.set()
                await websocket.send_json({"type": "cancelled"})
                continue

            if event_type == "ping":
                await websocket.send_json({"type": "pong"})
                continue

            await websocket.send_json(
                {"type": "error", "message": f"Unknown event type: {event_type}"}
            )
    except WebSocketDisconnect:
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


async def _handle_turn(
    websocket: WebSocket,
    audio_bytes: bytes,
    mime_type: str,
    conversation: ConversationContext,
    cancel_event: asyncio.Event,
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
        )
    except SpeechToTextError as exc:
        await websocket.send_json({"type": "error", "message": str(exc)})
        return

    # Phase 2: use English translation (from parallel translate call) as the
    # text that will eventually be fed to the LLM agent.  For now we still use
    # the stub response, but we emit the english_text in the transcript event
    # so the frontend can show both.
    english_text = stt_result.english_text or (
        stt_result.text if stt_result.language == "en" else None
    )

    response_text = _stub_response_for_language(stt_result.language)
    response_language = stt_result.language

    await websocket.send_json(
        {
            "type": "transcript",
            "text": stt_result.text,
            "englishText": english_text,
            "language": stt_result.language,
            "detectedLanguage": stt_result.detected_language,
        }
    )
    await websocket.send_json(
        {
            "type": "agent_response",
            "text": response_text,
            "language": response_language,
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


def _stub_response_for_language(language: str) -> str:
    if language.startswith("ur"):
        return (
            "میں نے آپ کی بات سن لی ہے۔ ابتدائی نمونے میں ابھی پالیسی تلاش شامل "
            "نہیں ہے، اس لیے آپ کا کیس انسانی نمائندے کو بھیجا جائے گا۔"
        )

    return (
        "I heard your claim. This first prototype has not connected policy lookup yet, "
        "so I will route this case to a human representative."
    )
