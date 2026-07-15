"""
Prompt package for the airline assistant agent.

This package contains all prompts used by the LangGraph agent,
organized by function for better maintainability.
"""

from .planner import (
    PLANNER_SYSTEM_MESSAGE,
    build_planner_user_prompt,
)
from .query_rewriter import (
    QUERY_REWRITER_SYSTEM_MESSAGE,
    build_query_rewriter_user_prompt,
)
from .response_generator import (
    RESPONSE_GENERATOR_SYSTEM_MESSAGE,
)
from .translator import (
    URDU_TRANSLATOR_SYSTEM_MESSAGE,
    build_urdu_translator_user_prompt,
)
from .shared import (
    TRUST_HIERARCHY,
    DATA_ONLY_WARNING,
    SAFETY_RULES,
    AIRLINE_ASSISTANT_IDENTITY,
    URDU_LANGUAGE_RULES,
    OUTPUT_FORMAT_JSON,
    INTERNAL_DETAILS_PROTECTION,
    FOLLOW_UP_QUESTIONS,
    PROGRESSIVE_INFORMATION_GATHERING,
    PROACTIVE_GUIDANCE,
    INTENT_AWARE_RESPONSES,
    CONCISE_VOICE_RESPONSES,
    ERROR_RECOVERY,
    BOOKING_MODIFICATION_CONFIRMATION,
)

__all__ = [
    # Planner
    "PLANNER_SYSTEM_MESSAGE",
    "build_planner_user_prompt",
    # Query Rewriter
    "QUERY_REWRITER_SYSTEM_MESSAGE",
    "build_query_rewriter_user_prompt",
    # Response Generator
    "RESPONSE_GENERATOR_SYSTEM_MESSAGE",
    # Translator
    "URDU_TRANSLATOR_SYSTEM_MESSAGE",
    "build_urdu_translator_user_prompt",
    # Shared
    "TRUST_HIERARCHY",
    "DATA_ONLY_WARNING",
    "SAFETY_RULES",
    "AIRLINE_ASSISTANT_IDENTITY",
    "URDU_LANGUAGE_RULES",
    "OUTPUT_FORMAT_JSON",
    "INTERNAL_DETAILS_PROTECTION",
    "FOLLOW_UP_QUESTIONS",
    "PROGRESSIVE_INFORMATION_GATHERING",
    "PROACTIVE_GUIDANCE",
    "INTENT_AWARE_RESPONSES",
    "CONCISE_VOICE_RESPONSES",
    "ERROR_RECOVERY",
    "BOOKING_MODIFICATION_CONFIRMATION",
]
