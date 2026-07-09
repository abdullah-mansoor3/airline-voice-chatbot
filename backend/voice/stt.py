from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass

from groq import AsyncGroq

# Turbo is faster/cheaper for transcription; only the full v3 model supports translate.
_TRANSCRIBE_MODEL = "whisper-large-v3-turbo"
_TRANSLATE_MODEL = "whisper-large-v3"


class SpeechToTextError(RuntimeError):
    pass


@dataclass(frozen=True)
class TranscriptionResult:
    text: str
    """Native-script transcript (Urdu in Nastaliq, English, or code-switched)."""
    language: str
    """Normalised two-letter code: 'ur' or 'en'."""
    detected_language: str
    """Raw language tag returned by Whisper (e.g. 'urdu', 'english')."""
    english_text: str | None = None
    """English translation from the parallel translate call.
    None when Whisper already transcribed in English (no translation needed)."""


async def transcribe_audio(
    audio_bytes: bytes,
    *,
    filename: str,
    content_type: str,
    language_hint: str | None = None,
) -> TranscriptionResult:
    """Fire ``transcribe`` and ``translate`` in parallel on the same audio buffer.

    - ``transcribe`` returns the native-script text plus Whisper's detected language.
    - ``translate`` always returns English regardless of source language.

    If Whisper already detected English the translate result is redundant, so we
    skip storing it separately (set ``english_text = None`` and use ``text`` directly).
    """
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise SpeechToTextError("GROQ_API_KEY is missing; cannot transcribe audio.")

    client = AsyncGroq(api_key=api_key)

    transcribe_kwargs = {
        "file": (filename, audio_bytes, content_type),
        "model": _TRANSCRIBE_MODEL,
        "response_format": "verbose_json",
    }
    if language_hint in {"en", "ur"}:
        transcribe_kwargs["language"] = language_hint

    transcribe_coro = client.audio.transcriptions.create(**transcribe_kwargs)
    translate_coro = client.audio.translations.create(
        file=(filename, audio_bytes, content_type),
        model=_TRANSLATE_MODEL,
        response_format="verbose_json",
    )

    try:
        transcribe_result, translate_result = await asyncio.gather(
            transcribe_coro, translate_coro
        )
    except Exception as exc:
        raise SpeechToTextError(f"Groq STT failed: {exc}") from exc

    text = (getattr(transcribe_result, "text", None) or "").strip()
    detected_language = (
        getattr(transcribe_result, "language", None) or "unknown"
    ).lower()
    language = language_hint if language_hint in {"en", "ur"} else normalize_supported_language(detected_language)

    if not text:
        raise SpeechToTextError("Groq STT returned an empty transcript.")

    # If already English, the translation is the same text — skip redundant storage.
    english_text: str | None = (
        getattr(translate_result, "text", None) or ""
    ).strip() or None
    if language == "en":
        english_text = None

    return TranscriptionResult(
        text=text,
        language=language,
        detected_language=detected_language,
        english_text=english_text,
    )


def normalize_supported_language(language: str) -> str:
    normalized = language.lower().strip().split("-")[0].split("_")[0]
    if normalized in {"en", "eng", "english"}:
        return "en"
    if normalized in {"ur", "urd", "urdu"}:
        return "ur"
    return "ur"
