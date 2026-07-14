from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass
from typing import Any

from groq import AsyncGroq

_TRANSCRIBE_MODEL = "whisper-large-v3"
_TRANSLATE_MODEL = "whisper-large-v3"
_DOMAIN_PROMPT = "پی آئی اے، ایئربلو، سیرین، ریفنڈ، منسوخ، ڈیلے، سامان، بکنگ، منسوخی"

class SpeechToTextError(RuntimeError):
    pass

@dataclass(frozen=True)
class TranscriptionResult:
    text: str
    language: str
    detected_language: str
    english_text: str | None = None

def _filter_segments(segments: list[Any]) -> str:
    valid_text = []
    for seg in segments:
        seg_dict = seg if isinstance(seg, dict) else getattr(seg, "__dict__", {})
        if not seg_dict and hasattr(seg, "model_dump"):
            seg_dict = seg.model_dump()
        no_speech = seg_dict.get("no_speech_prob", 0.0)
        avg_logprob = seg_dict.get("avg_logprob", 0.0)
        text = seg_dict.get("text", "")
        if no_speech > 0.6 and avg_logprob < -1.0:
            continue
        valid_text.append(text)
    return "".join(valid_text).strip()

def _extract_text(result: Any) -> str:
    segments = getattr(result, "segments", [])
    if segments:
        filtered = _filter_segments(segments)
        if filtered:
            return filtered
    return (getattr(result, "text", None) or "").strip()

async def transcribe_audio(
    audio_bytes: bytes,
    *,
    filename: str,
    content_type: str,
    language_hint: str | None = None,
) -> TranscriptionResult:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise SpeechToTextError("GROQ_API_KEY is missing; cannot transcribe audio.")

    client = AsyncGroq(api_key=api_key)
    file_tuple = (filename, audio_bytes, content_type)

    if language_hint is None:
        try:
            r1, r2, r3 = await asyncio.gather(
                client.audio.transcriptions.create(
                    file=file_tuple, model=_TRANSCRIBE_MODEL, response_format="verbose_json"
                ),
                client.audio.transcriptions.create(
                    file=file_tuple, model=_TRANSCRIBE_MODEL, response_format="verbose_json", language="ur", prompt=_DOMAIN_PROMPT
                ),
                client.audio.translations.create(
                    file=file_tuple, model=_TRANSLATE_MODEL, response_format="verbose_json"
                ),
            )
        except Exception as exc:
            raise SpeechToTextError(f"Groq STT failed: {exc}") from exc

        text1 = _extract_text(r1)
        text2 = _extract_text(r2)
        text3 = _extract_text(r3)

        detected_language = (getattr(r1, "language", None) or "unknown").lower()
        norm_lang = normalize_supported_language(detected_language)
        script_en = _language_from_script(text1) == "en"

        if norm_lang == "en" or script_en:
            text = text1
            language = "en"
            english_text = None
        else:
            text = text2
            language = "ur"
            english_text = text3 if text3 and not _is_useless_translation(text3) else None

            # Sanity checks/logging
            if not _has_urdu_script(text) and not _has_devanagari(text):
                print(f"Warning: Forced Urdu call produced non-Urdu script: {text}")

    else:
        transcribe_kwargs: dict[str, object] = {
            "file": file_tuple,
            "model": _TRANSCRIBE_MODEL,
            "response_format": "verbose_json",
        }
        if language_hint == "en":
            transcribe_kwargs["language"] = "en"
        elif language_hint == "ur":
            transcribe_kwargs["language"] = "ur"
            transcribe_kwargs["prompt"] = _DOMAIN_PROMPT

        try:
            transcribe_result, translate_result = await asyncio.gather(
                client.audio.transcriptions.create(**transcribe_kwargs),
                client.audio.translations.create(
                    file=file_tuple, model=_TRANSLATE_MODEL, response_format="verbose_json"
                ),
            )
        except Exception as exc:
            raise SpeechToTextError(f"Groq STT failed: {exc}") from exc

        text = _extract_text(transcribe_result)
        detected_language = (getattr(transcribe_result, "language", None) or "unknown").lower()
        language = language_hint

        english_text = _extract_text(translate_result)
        if english_text and _is_useless_translation(english_text):
            english_text = None
        if language == "en":
            english_text = None

    if not text:
        raise SpeechToTextError("Groq STT returned an empty transcript.")

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
    script_lang = _language_from_script(text)
    whisper_lang = normalize_supported_language(detected_language)
    if script_lang == "en":
        return "en"
    if script_lang == "ur":
        return "ur"
    return whisper_lang

def _language_from_script(text: str) -> str | None:
    if _has_urdu_script(text):
        return "ur"
    if _has_devanagari(text):
        return "ur"
    letters = [char for char in text if char.isalpha()]
    if letters and all(ord(char) < 128 for char in letters):
        return "en"
    if re.search(r"[\u0600-\u06ff\u0900-\u097f]", text):
        return "ur"
    return None

def _has_urdu_script(text: str) -> bool:
    return any("\u0600" <= char <= "\u06ff" for char in text)

def _has_devanagari(text: str) -> bool:
    return any("\u0900" <= char <= "\u097f" for char in text)

def _is_useless_translation(text: str) -> bool:
    compact = text.strip()
    if len(compact) <= 2 and not any(char.isalnum() for char in compact):
        return True
    words = re.findall(r"[A-Za-z\u0600-\u06ff]+", compact)
    return len(words) == 0
