from __future__ import annotations

import random
import re

EXIT_PHRASES_EN = [
    r"\bbye\b",
    r"\bgoodbye\b",
    r"\bsee you\b",
    r"turn off voice",
    r"stop voice",
    r"end voice",
    r"exit voice",
    r"disable voice",
    r"stop listening",
    r"close voice mode",
]

EXIT_PHRASES_UR = [
    r"اللہ حافظ",
    r"خدا حافظ",
    r"بائی",
    r"بای",
    r"وائس بند",
    r"آواز بند",
    r"وائس موڈ بند",
    r"آواز بند کرو",
    r"سننا بند",
]

FILLERS_EN = [
    "Ok, let me work on it.",
    "One moment please.",
    "Sure, give me a second.",
]

FILLERS_UR = [
    "اچھا، ایک منٹ دیکھتا ہوں۔",
    "ٹھیک ہے، ابھی چیک کرتا ہوں۔",
    "جی، ایک لمحہ۔",
]

WAIT_PHRASES_EN = [
    "Please wait, I'm still working on your last request.",
    "Hold on, let me finish the previous answer first.",
]

WAIT_PHRASES_UR = [
    "براہ کرم انتظار کریں، میں ابھی پچھلے سوال پر کام کر رہا ہوں۔",
    "ذرا رکیں، پہلے پچھلا جواب مکمل ہو جانے دیں۔",
]

VOICE_MODE_END_ACK_EN = "Voice mode turned off. Goodbye!"
VOICE_MODE_END_ACK_UR = "وائس موڈ بند کر دیا گیا۔ اللہ حافظ!"


def is_exit_phrase(text: str, language: str = "en") -> bool:
    lowered = text.lower().strip()
    patterns = EXIT_PHRASES_EN + (EXIT_PHRASES_UR if language == "ur" else [])
    if language == "ur":
        patterns = EXIT_PHRASES_UR + EXIT_PHRASES_EN
    return any(re.search(pattern, lowered) or re.search(pattern, text) for pattern in patterns)


def pick_filler(language: str) -> str:
    pool = FILLERS_UR if language == "ur" else FILLERS_EN
    return random.choice(pool)


def pick_wait_phrase(language: str) -> str:
    pool = WAIT_PHRASES_UR if language == "ur" else WAIT_PHRASES_EN
    return random.choice(pool)


def voice_mode_end_ack(language: str) -> str:
    return VOICE_MODE_END_ACK_UR if language == "ur" else VOICE_MODE_END_ACK_EN
