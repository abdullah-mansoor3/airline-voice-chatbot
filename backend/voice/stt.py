from __future__ import annotations

import os
from dataclasses import dataclass

from groq import AsyncGroq


class SpeechToTextError(RuntimeError):
    pass


@dataclass(frozen=True)
class TranscriptionResult:
    text: str
    language: str
    detected_language: str


async def transcribe_audio(
    audio_bytes: bytes,
    *,
    filename: str,
    content_type: str,
) -> TranscriptionResult:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise SpeechToTextError("GROQ_API_KEY is missing; cannot transcribe audio.")

    client = AsyncGroq(api_key=api_key)

    try:
        result = await client.audio.transcriptions.create(
            file=(filename, audio_bytes, content_type),
            model="whisper-large-v3-turbo",
            response_format="verbose_json",
        )
    except Exception as exc:  # SDK exceptions vary by transport/version.
        raise SpeechToTextError(f"Groq STT failed: {exc}") from exc

    text = (getattr(result, "text", None) or "").strip()
    detected_language = (getattr(result, "language", None) or "unknown").lower()
    language = normalize_supported_language(detected_language)

    if not text:
        raise SpeechToTextError("Groq STT returned an empty transcript.")

    return TranscriptionResult(
        text=text,
        language=language,
        detected_language=detected_language,
    )


def normalize_supported_language(language: str) -> str:
    normalized = language.lower().split("-")[0].split("_")[0]
    if normalized == "en":
        return "en"
    return "ur"
