from __future__ import annotations

import re

_MARKDOWN_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_MARKDOWN_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]+\)")
_HTML_TAG = re.compile(r"<[^>]+>")
_CODE_FENCE = re.compile(r"```[\s\S]*?```")
_INLINE_CODE = re.compile(r"`([^`]+)`")
_BULLET = re.compile(r"^\s*[-*+]\s+", re.MULTILINE)
_NUMBERED = re.compile(r"^\s*\d+[.)]\s+", re.MULTILINE)
_HEADING = re.compile(r"^\s*#{1,6}\s+", re.MULTILINE)
_BOLD_ITALIC = re.compile(r"(\*\*|__|\*|_)(.*?)\1")
_CITATION_MARKER = re.compile(r"【[^】]+】|\[[0-9a-f-]{8,}\]")
_SPECIAL_CHARS = re.compile(r"[*#`|<>\\]")


def format_text_for_tts(text: str, language: str = "en") -> str:
    """Convert agent markdown into plain text suitable for speech synthesis."""
    if not text or not text.strip():
        return ""

    spoken = text.strip()
    spoken = _CODE_FENCE.sub(" ", spoken)
    spoken = _MARKDOWN_IMAGE.sub(r"\1", spoken)
    spoken = _MARKDOWN_LINK.sub(r"\1", spoken)
    spoken = _INLINE_CODE.sub(r"\1", spoken)
    spoken = _HTML_TAG.sub(" ", spoken)
    spoken = _HEADING.sub("", spoken)
    spoken = _BULLET.sub("", spoken)
    spoken = _NUMBERED.sub("", spoken)
    spoken = _CITATION_MARKER.sub("", spoken)

    for _ in range(3):
        updated = _BOLD_ITALIC.sub(r"\2", spoken)
        if updated == spoken:
            break
        spoken = updated

    spoken = _SPECIAL_CHARS.sub(" ", spoken)
    spoken = re.sub(r"\s*:\s*", ", ", spoken)
    spoken = re.sub(r"\s{2,}", " ", spoken)
    spoken = re.sub(r"\n{2,}", ". ", spoken)
    spoken = spoken.replace("\n", " ")
    spoken = _normalize_spoken_numbers(spoken, language)
    spoken = re.sub(r"\s+([,.!?])", r"\1", spoken)
    spoken = re.sub(r"\.{2,}", ".", spoken)
    return spoken.strip()


def _normalize_spoken_numbers(text: str, language: str) -> str:
    """Make common numeric patterns easier to read aloud."""
    if language == "ur":
        text = text.replace("PKR", "روپے")
        text = text.replace("Rs.", "روپے")
        text = text.replace("Rs", "روپے")
    else:
        text = text.replace("PKR", "Pakistani rupees")
        text = re.sub(r"\bRs\.?\s*", "rupees ", text)

    text = re.sub(
        r"\b(\d{1,2}):(\d{2})\b",
        lambda match: f"{match.group(1)} {match.group(2)}",
        text,
    )
    return text
