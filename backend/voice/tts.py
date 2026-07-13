from __future__ import annotations

"""Text-to-speech via edge-tts.

Phase 2 additions:
- Sentence-level streaming: ``stream_speech_sentences`` yields audio chunks as
  each sentence finishes synthesising, so the WebSocket handler can begin
  sending audio before the full response is ready.
- Voice selection by detected language (Urdu → UzmaNeural / AsadNeural,
  English → AriaNeural).
- ``synthesize_speech`` is kept for test endpoints and non-streaming paths.
"""

import asyncio
import re
from collections.abc import AsyncIterator

import edge_tts


class TextToSpeechError(RuntimeError):
    pass


VOICE_BY_LANGUAGE = {
    "ur": "ur-PK-UzmaNeural",   # primary Urdu female voice
    "en": "en-US-AriaNeural",   # primary English voice
}

# Alternate Urdu voice (male) — available as a fallback.
_URDU_MALE_VOICE = "ur-PK-AsadNeural"

# Sentence boundary pattern: split after .?! followed by whitespace or end of
# string.  We keep the delimiter attached to the preceding sentence so TTS
# prosody is correct.
_SENTENCE_SPLIT = re.compile(r"(?<=[.?!۔؟!])\s+")


def _voice_for(language: str) -> str:
    if language[:2] == "en":
        return VOICE_BY_LANGUAGE["en"]
    return VOICE_BY_LANGUAGE["ur"]


async def synthesize_speech(text: str, language: str) -> bytes:
    """Synthesise *text* to MP3 bytes (blocking, returns full audio at once).

    Use ``stream_speech_sentences`` in the WebSocket handler for lower latency.
    This function is kept for health-check / test endpoints.
    """
    voice = _voice_for(language)
    communicate = edge_tts.Communicate(text=text, voice=voice)
    audio = bytearray()

    try:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio.extend(chunk["data"])
    except Exception as exc:
        raise TextToSpeechError(f"edge-tts failed: {exc}") from exc

    if not audio:
        raise TextToSpeechError("edge-tts returned no audio.")

    return bytes(audio)


async def stream_speech_sentences(
    text: str,
    language: str,
    *,
    cancel_event: asyncio.Event | None = None,
) -> AsyncIterator[bytes]:
    """Yield MP3 audio chunks sentence by sentence for low-latency streaming.

    Splits *text* on sentence boundaries, synthesises each sentence with
    edge-tts, and yields the complete audio for that sentence as soon as it is
    ready.  The caller can start sending audio to the client immediately after
    the first sentence is yielded, without waiting for the full response.

    ``cancel_event`` — if provided and set, synthesis stops between sentences
    so the caller can abort on barge-in without waiting for the full text.
    """
    voice = _voice_for(language)
    sentences = _split_sentences(text)

    for sentence in sentences:
        if cancel_event is not None and cancel_event.is_set():
            return

        sentence = sentence.strip()
        if not sentence:
            continue

        communicate = edge_tts.Communicate(text=sentence, voice=voice)
        audio = bytearray()

        try:
            async for chunk in communicate.stream():
                if cancel_event is not None and cancel_event.is_set():
                    return
                if chunk["type"] == "audio":
                    audio.extend(chunk["data"])
        except Exception as exc:
            raise TextToSpeechError(f"edge-tts failed on sentence: {exc}") from exc

        if audio:
            yield bytes(audio)


def _split_sentences(text: str) -> list[str]:
    """Split text on sentence boundaries while preserving the delimiter."""
    parts = _SENTENCE_SPLIT.split(text)
    return [p for p in parts if p.strip()]
