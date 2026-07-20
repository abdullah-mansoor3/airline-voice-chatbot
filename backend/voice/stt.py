from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass
from typing import Any

from groq import AsyncGroq

_PRIMARY_MODEL = "openai/gpt-oss-120b"
_FALLBACK_MODEL = "openai/gpt-oss-20b"
_TRANSCRIBE_MODEL = "whisper-large-v3"
_DOMAIN_PROMPT = """یہ گفتگو پاکستان کی ایک ایئرلائن کسٹمر سپورٹ ایپ کے بارے میں ہے۔
پی آئی اے پاکستان انٹرنیشنل ایئرلائنز ایئربلو سیرین ایئر فلائی جناح ایئر سیال
فلائٹ پرواز بکنگ ریزرویشن ٹکٹ ریفنڈ ری شیڈول ری بک کینسل منسوخ ڈیلے تاخیر
بورڈنگ چیک ان گیٹ ٹرمینل سامان بیگیج کیری آن اضافی سامان کارگو
اسلام آباد کراچی لاہور پشاور کوئٹہ ملتان سکردو گلگت
دبئی جدہ ریاض مدینہ دوحہ مسقط ابوظہبی شارجہ استنبول
PNR Booking ID Reference Number Confirmation Number Seat Business Class Economy Window Seat Aisle Seat
انگریزی الفاظ اردو گفتگو میں شامل ہو سکتے ہیں۔"""

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

    # Validate audio size - need at least some audio data
    if len(audio_bytes) < 1000:
        raise SpeechToTextError(f"Audio too short ({len(audio_bytes)} bytes). Please speak for longer.")

    client = AsyncGroq(api_key=api_key)

    # Strip codec parameter from content_type for Groq compatibility
    # e.g., "audio/webm;codecs=opus" -> "audio/webm"
    clean_content_type = content_type.split(";")[0].strip()
    file_tuple = (filename, audio_bytes, clean_content_type)

    if language_hint is None:
        try:
            r1, r2 = await asyncio.gather(
                client.audio.transcriptions.create(
                    file=file_tuple, model=_TRANSCRIBE_MODEL, response_format="verbose_json"
                ),
                client.audio.transcriptions.create(
                    file=file_tuple, model=_TRANSCRIBE_MODEL, response_format="verbose_json", language="ur", prompt=_DOMAIN_PROMPT
                ),
            )
        except Exception as exc:
            raise SpeechToTextError(f"Groq STT failed: {exc}") from exc

        text1 = _extract_text(r1)
        text2 = _extract_text(r2)

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
            english_text = await _translate_text_urdu_to_english(text, client)

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
            transcribe_result = await client.audio.transcriptions.create(**transcribe_kwargs)
        except Exception as exc:
            raise SpeechToTextError(f"Groq STT failed: {exc}") from exc

        text = _extract_text(transcribe_result)
        detected_language = (getattr(transcribe_result, "language", None) or "unknown").lower()
        language = language_hint

        english_text = None
        if language == "ur":
            english_text = await _translate_text_urdu_to_english(text, client)

    if not text:
        raise SpeechToTextError("Groq STT returned an empty transcript.")

    if _is_hallucination(text):
        raise SpeechToTextError(
            f"No clear speech detected (possible silence or background noise). "
            f"Whisper returned: {text!r}"
        )

    # ── Short-transcript / single-word noise filter ───────────────────────────
    # Extract all meaningful word tokens (Latin + Urdu/Arabic scripts, min 2 chars each).
    word_tokens = re.findall(r"[A-Za-z\u0600-\u06ff\u0750-\u077f\u0900-\u097f]{2,}", text.strip())
    # A single word of ≤2 chars is almost always noise (ok, ha, یہ, ہا, etc.)
    if len(word_tokens) == 1 and len(word_tokens[0]) <= 2:
        raise SpeechToTextError(
            f"Single very short word — likely noise. Whisper returned: {text!r}"
        )
    # Zero meaningful words (e.g. only digits or punctuation)
    if len(word_tokens) == 0:
        raise SpeechToTextError(
            f"No recognisable words in transcript. Whisper returned: {text!r}"
        )

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

# Common Whisper hallucinations on silence / non-speech audio
_HALLUCINATION_PATTERNS = re.compile(
    r"^("
    r"thank\s+you[\.\!\?]?|thanks[\.\!\?]?|"
    r"شکریہ[\.\!\?]?|شکریا[\.\!\?]?|سکریا[\.\!\?]?|"
    r"ہاں[\.\!\?،]?|ہاہاہا[\.\!\?،]?|ہا\s?ہا[\.\!\?،]?|"
    r"ha\s?ha[\.\!\?]?|haha[\.\!\?]?|"
    r"bye[\.\!\?]?|goodbye[\.\!\?]?|ok[\.\!\?]?|okay[\.\!\?]?|"
    r"سبسکرائب[\w\s]*|subscribe[\w\s]*|"  # common YT hallucination
    r"\[[\w\s]*\]|"  # [Music], [Applause], etc.
    r"\([\w\s]*\)"  # (laughter), (coughing), etc.
    r")$",
    re.IGNORECASE | re.UNICODE,
)

def _is_hallucination(text: str) -> bool:
    """Return True if the transcript looks like a Whisper silence hallucination."""
    stripped = text.strip().strip(".,!؟?،")
    if not stripped:
        return True
    # Very short outputs that are just punctuation / symbols
    if len(stripped) <= 3 and not any(c.isalnum() for c in stripped):
        return True
    # Match known hallucination patterns
    if _HALLUCINATION_PATTERNS.match(stripped):
        return True
    # Repetitive single character (e.g. "ہہہہہہ")
    unique_chars = set(c for c in stripped if c.isalpha())
    if unique_chars and len(unique_chars) == 1 and len(stripped) >= 3:
        return True
    return False

async def _translate_text_urdu_to_english(text: str, client: AsyncGroq) -> str | None:
    """Translate Urdu text to English using LLM chat completions."""
    if not text or not _has_urdu_script(text):
        return None

    try:
        response = await client.chat.completions.create(
            model=_PRIMARY_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": "You are a professional translator. Translate the given Urdu text to English. Return only the English translation, nothing else. Do not add explanations or notes.",
                },
                {
                    "role": "user",
                    "content": text,
                },
            ],
            temperature=0.2,
            max_tokens=1024,
        )
        translated = response.choices[0].message.content
        if translated and not _is_useless_translation(translated):
            return translated.strip()
    except Exception as exc:
        print(f"LLM translation failed: {exc}")
        try:
            response = await client.chat.completions.create(
                model=_FALLBACK_MODEL,
                messages=[
                    {
                        "role": "system",
                        "content": """
                        You are translating speech from a Pakistani airline customer support conversation.

                        The input comes from Whisper speech recognition and may contain:

                        - recognition mistakes
                        - Urdu mixed with English
                        - Roman Urdu
                        - airline names
                        - airport names
                        - flight numbers
                        - booking IDs
                        - PNRs
                        - English aviation terminology spoken in Urdu

                        Your task is to recover the intended meaning.

                        Rules:

                        • Preserve airline names exactly whenever possible.

                        • Preserve airport codes exactly.

                        • Preserve flight numbers exactly.

                        • Preserve booking IDs exactly.

                        • Preserve PNRs exactly.

                        • Do NOT translate proper nouns.

                        • Correct obvious Whisper mistakes using context.

                        • If a word is clearly intended to be an airline or aviation term, recover the intended English spelling.

                        Examples:

                        پی آئی اے
                        → PIA

                        ایئربلو
                        → Airblue

                        سیرین
                        → Serene Air

                        فلائی جناح
                        → Fly Jinnah

                        اسلام آباد
                        → Islamabad

                        ریفنڈ
                        → refund

                        منسوخ
                        → cancel

                        Only return the translated English sentence.

                        Do not explain anything.""",
                    },
                    {
                        "role": "user",
                        "content": text,
                    },
                ],
                temperature=0.2,
                max_tokens=1024,
            )
            translated = response.choices[0].message.content
            if translated and not _is_useless_translation(translated):
                return translated.strip()
        except Exception as fallback_exc:
            print(f"Fallback LLM translation failed: {fallback_exc}")
    return None
