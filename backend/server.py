from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from .voice.stt import SpeechToTextError, transcribe_audio
from .voice.tts import TextToSpeechError, synthesize_speech

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

    try:
        await websocket.send_json({"type": "ready"})
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
                await websocket.send_json({"type": "recording_started"})
                continue

            if event_type == "stop":
                await _handle_turn(websocket, bytes(audio_buffer), mime_type)
                audio_buffer = bytearray()
                continue

            if event_type == "ping":
                await websocket.send_json({"type": "pong"})
                continue

            await websocket.send_json(
                {"type": "error", "message": f"Unknown event type: {event_type}"}
            )
    except WebSocketDisconnect:
        return


async def _handle_turn(
    websocket: WebSocket, audio_bytes: bytes, mime_type: str
) -> None:
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
        )
    except SpeechToTextError as exc:
        await websocket.send_json({"type": "error", "message": str(exc)})
        return

    response_text = _stub_response_for_language(stt_result.language)
    response_language = stt_result.language

    await websocket.send_json(
        {
            "type": "transcript",
            "text": stt_result.text,
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
    await websocket.send_json({"type": "processing", "stage": "tts"})

    try:
        tts_audio = await synthesize_speech(response_text, response_language)
    except TextToSpeechError as exc:
        await websocket.send_json({"type": "error", "message": str(exc)})
        return

    await websocket.send_json(
        {
            "type": "tts_audio",
            "mimeType": "audio/mpeg",
            "bytes": len(tts_audio),
        }
    )
    await websocket.send_bytes(tts_audio)
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
