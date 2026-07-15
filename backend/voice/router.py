"""
Voice Router - Deterministic low-latency command routing for Voice AI.

This module provides a lightweight preprocessing stage that executes BEFORE
the LangGraph agent. It handles common voice commands immediately without
invoking the full agent, reducing latency for simple interactions.

Design Principles:
- Deterministic: No ML, no LLM, no embeddings, no external APIs
- Fast: O(n) runtime over normalized text
- Safe: Prefer false negatives over false positives
- Priority-based: Stop after first confident match

Router Flow:
1. Normalize text (lowercase, trim, collapse spaces, remove repetitions)
2. Evaluate categories in priority order
3. Return immediately on first match
4. Default to AGENT if no match
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class RouteResult:
    """Result of voice routing."""
    category: Literal[
        "exit_voice_mode",
        "stop_speaking",
        "repeat",
        "pause_resume",
        "help",
        "greeting",
        "thanks",
        "goodbye",
        "prompt_injection",
        "irrelevant",
        "agent",
    ]
    response_en: str | None = None
    response_ur: str | None = None
    should_interrupt: bool = False


def _normalize_text(text: str) -> str:
    """
    Normalize text for robust regex matching.

    Operations:
    - Lowercase English
    - Trim whitespace
    - Collapse repeated spaces
    - Remove repeated punctuation
    - Remove excessive elongation (e.g., "helloooo" -> "hello")
    - Normalize Urdu unicode variants where practical

    Examples:
        "helloooo" -> "hello"
        "   bye   " -> "bye"
        "السلام علیکم" -> "السلام علیکم"
    """
    if not text:
        return ""

    # Lowercase for English
    normalized = text.lower()

    # Trim whitespace
    normalized = normalized.strip()

    # Collapse repeated spaces
    normalized = re.sub(r"\s+", " ", normalized)

    # Remove repeated punctuation (e.g., "!!!" -> "!")
    normalized = re.sub(r"([!?.,])\1{2,}", r"\1", normalized)

    # Remove excessive elongation (e.g., "helloooo" -> "hello")
    # Keep at most 2 repeated characters for common words
    normalized = re.sub(r"(.)\1{3,}", r"\1\1", normalized)

    return normalized


def _is_exit_voice_mode(normalized: str) -> bool:
    """
    Detect exit voice mode commands.

    Patterns:
    - exit voice mode
    - stop voice mode
    - end voice mode
    - quit voice
    - voice mode off
    - Urdu: آواز موڈ بند
    """
    patterns = [
        r"\bexit\s+voice\s+mode\b",
        r"\bstop\s+voice\s+mode\b",
        r"\bend\s+voice\s+mode\b",
        r"\bquit\s+voice\b",
        r"\bvoice\s+mode\s+off\b",
        r"آواز\s+موڈ\s+بند",
        r"وائس\s+موڈ\s+بند",
    ]
    return any(re.search(pattern, normalized) for pattern in patterns)


def _is_stop_speaking(normalized: str) -> bool:
    """
    Detect stop speaking / cancel commands.

    Patterns:
    - stop
    - stop speaking
    - cancel
    - cancel response
    - be quiet
    - shut up
    - Urdu: خاموش، بس، رک جاؤ، وقفہ
    """
    patterns = [
        r"\bstop\s+speaking\b",
        r"\bcancel\s+response\b",
        r"\bbe\s+quiet\b",
        r"\bshut\s+up\b",
        r"\bخاموش\b",
        r"\bبس\b",
        r"\bرک\s+جاؤ\b",
        r"\bوقفہ\b",
    ]
    # Single word "stop" or "cancel" only if it's the entire text
    # to avoid false positives like "stop my flight"
    if normalized in ("stop", "cancel"):
        return True
    return any(re.search(pattern, normalized) for pattern in patterns)


def _is_repeat(normalized: str) -> bool:
    """
    Detect repeat commands.

    Patterns:
    - repeat
    - say again
    - repeat that
    - can you repeat
    - Urdu: دوبارہ، پھر بولیں، یہ دوبارہ کہیں
    """
    patterns = [
        r"\bsay\s+again\b",
        r"\brepeat\s+that\b",
        r"\bcan\s+you\s+repeat\b",
        r"\bدوبارہ\b",
        r"\bپھر\s+بولیں\b",
        r"\bیہ\s+دوبارہ\s+کہیں\b",
    ]
    # Single word "repeat" only if it's the entire text
    if normalized == "repeat":
        return True
    return any(re.search(pattern, normalized) for pattern in patterns)


def _is_pause_resume(normalized: str) -> bool:
    """
    Detect pause/resume commands.

    Patterns:
    - pause
    - resume
    - continue
    - Urdu: روکو، جاری رکھو
    """
    patterns = [
        r"\bروکو\b",
        r"\bجاری\s+رکھو\b",
    ]
    # Single word commands only if entire text
    if normalized in ("pause", "resume", "continue"):
        return True
    return any(re.search(pattern, normalized) for pattern in patterns)


def _is_help(normalized: str) -> bool:
    """
    Detect help commands.

    Patterns:
    - help
    - what can you do
    - Urdu: مدد، تم کیا کر سکتے ہو

    IMPORTANT: Must NOT match "help me cancel my booking"
    Only match standalone help requests.
    """
    patterns = [
        r"\bwhat\s+can\s+you\s+do\b",
        r"\bمدد\b",
        r"\bتم\s+کیا\s+کر\s+سکتے\s+ہو\b",
    ]
    # Single word "help" only if entire text
    if normalized == "help":
        return True
    return any(re.search(pattern, normalized) for pattern in patterns)


def _is_greeting(normalized: str) -> bool:
    """
    Detect greeting commands.

    Patterns:
    - hello, hi, hey
    - assalamualaikum
    - good morning, good evening
    - Urdu: السلام علیکم

    IMPORTANT: Must NOT match "hello i need to cancel my flight"
    Only match standalone greetings.
    """
    patterns = [
        r"\bgood\s+(morning|evening|afternoon)\b",
        r"\bالسلام\s+علیکم\b",
        r"\bالسلام\s+علیکوم\b",
    ]
    # Single word greetings only if entire text
    if normalized in ("hello", "hi", "hey"):
        return True
    return any(re.search(pattern, normalized) for pattern in patterns)


def _is_thanks(normalized: str) -> bool:
    """
    Detect thanks/acknowledgement.

    Patterns:
    - thanks, thank you
    - Urdu: جزاک اللہ، شکریہ، مہربانی

    IMPORTANT: Must NOT match "thanks, now cancel my ticket"
    Only match standalone thanks.
    """
    patterns = [
        r"\bthank\s+you\b",
        r"\bجزاک\s+اللہ\b",
        r"\bشکریہ\b",
        r"\bمہربانی\b",
    ]
    # Single word "thanks" only if entire text
    if normalized == "thanks":
        return True
    return any(re.search(pattern, normalized) for pattern in patterns)


def _is_goodbye(normalized: str) -> bool:
    """
    Detect goodbye commands.

    Patterns:
    - bye, goodbye, see you
    - Urdu: اللہ حافظ، خدا حافظ

    IMPORTANT: Must NOT match "bye for now, i'll be back"
    Only match standalone goodbyes.
    """
    patterns = [
        r"\bgoodbye\b",
        r"\bsee\s+you\b",
        r"\bاللہ\s+حافظ\b",
        r"\bخدا\s+حافظ\b",
    ]
    # Single word "bye" only if entire text
    if normalized == "bye":
        return True
    return any(re.search(pattern, normalized) for pattern in patterns)


def _is_prompt_injection(normalized: str) -> bool:
    """
    Detect prompt injection attempts.

    Reuses existing security patterns from the agent.
    This is a security-critical check.
    """
    # Patterns from existing security checks
    patterns = [
        r"ignore\s+(all\s+)?(previous\s+)?(instructions|prompts?|rules)",
        r"forget\s+(all\s+)?(previous\s+)?(instructions|prompts?|rules)",
        r"override\s+(all\s+)?(instructions|prompts?|rules)",
        r"new\s+(instructions|prompts?|rules)",
        r"system\s*:\s*",
        r"developer\s*:\s*",
        r"admin\s*:\s*",
        r"reveal\s+(your\s+)?(prompt|instructions|system)",
        r"show\s+(your\s+)?(prompt|instructions|system)",
        r"tell\s+me\s+(your\s+)?(prompt|instructions|system)",
        r"what\s+(are\s+)?your\s+(instructions|prompts?|system)",
        r"print\s+(your\s+)?(prompt|instructions|system)",
        r"output\s+(your\s+)?(prompt|instructions|system)",
        r"dump\s+(your\s+)?(prompt|instructions|system)",
        r"execute\s+(code|command|script)",
        r"run\s+(code|command|script)",
        r"eval\s+(code|command|script)",
        r"python\s*:",
        r"javascript\s*:",
        r"bash\s*:",
        r"shell\s*:",
        r"```",
        r"<script",
        r"<iframe",
        r"<object",
        r"onload\s*=",
        r"onerror\s*=",
    ]
    return any(re.search(pattern, normalized, re.IGNORECASE) for pattern in patterns)


def _is_irrelevant(normalized: str) -> bool:
    """
    Detect irrelevant non-airline conversation.

    Patterns:
    - tell me a joke
    - sing a song
    - who is messi
    - what is bitcoin
    - how old are you
    - who made you

    IMPORTANT: Conservative matching to avoid false positives.
    Only match clearly non-airline topics.
    """
    patterns = [
        r"\btell\s+me\s+a\s+joke\b",
        r"\bsing\s+(a\s+)?song\b",
        r"\bwho\s+is\s+(messi|ronaldo|cricket|football|actor|actress|singer)\b",
        r"\bwhat\s+is\s+(bitcoin|crypto|blockchain|nft|ai|artificial\s+intelligence)\b",
        r"\bhow\s+old\s+are\s+you\b",
        r"\bwho\s+made\s+you\b",
        r"\bwho\s+created\s+you\b",
        r"\bwhat\s+is\s+your\s+name\b",
        r"\bare\s+you\s+(human|real|alive)\b",
        r"\btranslate\s+this\b",
        r"\bdefine\s+\w+\b",
    ]
    return any(re.search(pattern, normalized) for pattern in patterns)


def route_voice_command(
    text: str,
    language: str,
) -> RouteResult:
    """
    Main router function - evaluates categories in priority order.

    Priority Order:
    1. Exit Voice Mode
    2. Stop Speaking
    3. Repeat
    4. Pause/Resume
    5. Help
    6. Greeting
    7. Thanks
    8. Goodbye
    9. Prompt Injection
    10. Irrelevant Conversation
    11. Agent (default)

    Returns immediately on first confident match.
    """
    normalized = _normalize_text(text)

    # 1. Exit Voice Mode
    if _is_exit_voice_mode(normalized):
        return RouteResult(
            category="exit_voice_mode",
            response_en="Voice mode has been exited.",
            response_ur="آواز موڈ بند کر دیا گیا ہے۔",
            should_interrupt=True,
        )

    # 2. Stop Speaking
    if _is_stop_speaking(normalized):
        return RouteResult(
            category="stop_speaking",
            response_en="Speaking stopped.",
            response_ur="بولنا رک دیا گیا۔",
            should_interrupt=True,
        )

    # 3. Repeat
    if _is_repeat(normalized):
        return RouteResult(
            category="repeat",
            response_en=None,  # Will use previous response from context
            response_ur=None,
            should_interrupt=True,
        )

    # 4. Pause/Resume
    if _is_pause_resume(normalized):
        return RouteResult(
            category="pause_resume",
            response_en="Pause/resume toggled.",
            response_ur="روکنا/جاری رکھنا تبدیل ہو گیا۔",
            should_interrupt=True,
        )

    # 5. Help
    if _is_help(normalized):
        return RouteResult(
            category="help",
            response_en=(
                "I can help you with flight bookings, cancellations, refunds, "
                "baggage policies, flight status, and other airline services. "
                "Just ask your question in English or Urdu."
            ),
            response_ur=(
                "میں آپ کو فلائٹ بکنگ، کینسل، ریفنڈ، بیگیج پالیسی، فلائٹ اسٹیٹس، "
                "اور دیگر ایئرلائن سروسز میں مدد کر سکتا ہوں۔ "
                "صرف اپنا سوال انگریزی یا اردو میں پوچھیں۔"
            ),
            should_interrupt=False,
        )

    # 6. Greeting
    if _is_greeting(normalized):
        return RouteResult(
            category="greeting",
            response_en="Hello! How can I help you with your airline needs today?",
            response_ur="السلام علیکم! میں آپ کی ایئرلائن کی ضروریات میں کیسے مدد کر سکتا ہوں؟",
            should_interrupt=False,
        )

    # 7. Thanks
    if _is_thanks(normalized):
        return RouteResult(
            category="thanks",
            response_en="You're welcome! Is there anything else I can help you with?",
            response_ur="شکریہ! کیا میں آپ کی اور مدد کر سکتا ہوں؟",
            should_interrupt=False,
        )

    # 8. Goodbye
    if _is_goodbye(normalized):
        return RouteResult(
            category="goodbye",
            response_en="Goodbye! Have a great day.",
            response_ur="اللہ حافظ! آپ کا دن اچھا گزرے۔",
            should_interrupt=False,
        )

    # 9. Prompt Injection (security)
    if _is_prompt_injection(normalized):
        return RouteResult(
            category="prompt_injection",
            response_en="I cannot process that request. Please ask a genuine airline-related question.",
            response_ur="میں اس درخواست کو پروسیس نہیں کر سکتا۔ براہ کرم ایک حقیقی ایئرلائن سوال پوچھیں۔",
            should_interrupt=False,
        )

    # 10. Irrelevant Conversation
    if _is_irrelevant(normalized):
        return RouteResult(
            category="irrelevant",
            response_en=(
                "I'm designed to help with airline-related questions only. "
                "Please ask about flights, bookings, refunds, baggage, or other airline services."
            ),
            response_ur=(
                "میں صرف ایئرلائن سے متعلق سوالات میں مدد کرنے کے لیے بنایا گیا ہے۔ "
                "براہ کرم فلائٹس، بکنگ، ریفنڈ، بیگیج، یا دیگر ایئرلائن سروسز کے بارے میں پوچھیں۔"
            ),
            should_interrupt=False,
        )

    # 11. Agent (default - continue to LangGraph)
    return RouteResult(
        category="agent",
        response_en=None,
        response_ur=None,
        should_interrupt=False,
    )
