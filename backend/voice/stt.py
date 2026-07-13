from __future__ import annotations

import asyncio
import os
import re
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

    transcribe_kwargs: dict[str, object] = {
        "file": (filename, audio_bytes, content_type),
        "model": _TRANSCRIBE_MODEL,
        "response_format": "verbose_json",
    }
    if language_hint == "en":
        transcribe_kwargs["language"] = "en"
    elif language_hint == "ur":
        transcribe_kwargs["language"] = "ur"

    translate_coro = client.audio.translations.create(
        file=(filename, audio_bytes, content_type),
        model=_TRANSLATE_MODEL,
        response_format="verbose_json",
    )

    try:
        transcribe_result, translate_result = await asyncio.gather(
            client.audio.transcriptions.create(**transcribe_kwargs),
            translate_coro,
        )
    except Exception as exc:
        raise SpeechToTextError(f"Groq STT failed: {exc}") from exc

    text = (getattr(transcribe_result, "text", None) or "").strip()
    detected_language = (
        getattr(transcribe_result, "language", None) or "unknown"
    ).lower()

    if language_hint in {"en", "ur"}:
        language = language_hint
    else:
        language = infer_language_from_text(text, detected_language)

    if language == "ur" and language_hint != "en":
        has_devanagari = any("\u0900" <= char <= "\u097f" for char in text)
        has_arabic = any("\u0600" <= char <= "\u06ff" for char in text)
        is_ascii_english = _language_from_script(text) == "en"
        if has_devanagari or (not has_arabic and not is_ascii_english):
            text = await _transcribe_with_urdu_script(
                client,
                audio_bytes=audio_bytes,
                filename=filename,
                content_type=content_type,
                fallback_text=text,
            )

    if not text:
        raise SpeechToTextError("Groq STT returned an empty transcript.")

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
    return "ur"


def infer_language_from_text(text: str, detected_language: str) -> str:
    """Map Whisper tags and transcript script to supported en/ur labels."""
    script_lang = _language_from_script(text)
    whisper_lang = normalize_supported_language(detected_language)
    if script_lang == "en":
        return "en"
    if script_lang == "ur":
        return "ur"
    return whisper_lang


def _language_from_script(text: str) -> str | None:
    if any("\u0600" <= char <= "\u06ff" for char in text):
        return "ur"
    if any("\u0900" <= char <= "\u097f" for char in text):
        return "ur"
    letters = [char for char in text if char.isalpha()]
    if letters and all(ord(char) < 128 for char in letters):
        return "en"
    if re.search(r"[\u0600-\u06ff\u0900-\u097f]", text):
        return "ur"
    return None


async def _transcribe_with_urdu_script(
    client: AsyncGroq,
    *,
    audio_bytes: bytes,
    filename: str,
    content_type: str,
    fallback_text: str,
) -> str:
    """Re-transcribe with an Urdu hint so Hindi/other detections use Urdu script."""
    if _language_from_script(fallback_text) == "ur":
        return fallback_text
    try:
        result = await client.audio.transcriptions.create(
            file=(filename, audio_bytes, content_type),
            model=_TRANSCRIBE_MODEL,
            response_format="verbose_json",
            language="ur",
        )
        text = (getattr(result, "text", None) or "").strip()
        return text or fallback_text
    except Exception:
        return fallback_text
