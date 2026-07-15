"""
Response generator prompt for the main agent.

This prompt is used by the LangGraph agent node to generate responses
to user queries.
"""

from .shared import (
    AIRLINE_ASSISTANT_IDENTITY,
    BOOKING_MODIFICATION_CONFIRMATION,
    CONCISE_VOICE_RESPONSES,
    DATA_ONLY_WARNING,
    ERROR_RECOVERY,
    FOLLOW_UP_QUESTIONS,
    INTERNAL_DETAILS_PROTECTION,
    INTENT_AWARE_RESPONSES,
    OUTPUT_FORMAT_JSON,
    PROACTIVE_GUIDANCE,
    PROGRESSIVE_INFORMATION_GATHERING,
    SAFETY_RULES,
    TRUST_HIERARCHY,
    URDU_LANGUAGE_RULES,
)


RESPONSE_GENERATOR_SYSTEM_MESSAGE = """{airline_identity}

{trust_hierarchy}

{internal_details_protection}

{safety_rules}

{urdu_language_rules}

{follow_up_questions}

{progressive_information_gathering}

{proactive_guidance}

{intent_aware_responses}

{concise_voice_responses}

{error_recovery}

{booking_modification_confirmation}

When policy clauses are provided, ground legal answers only in those clauses. When no policy clauses are provided, answer from conversation memory, datetime context, flight results, or order context as appropriate. If flight results are empty, say no flights were found for that route/date. Stick to your role as an assistant, not a lawyer.

{output_format_json}""".format(
    airline_identity=AIRLINE_ASSISTANT_IDENTITY,
    trust_hierarchy=TRUST_HIERARCHY,
    internal_details_protection=INTERNAL_DETAILS_PROTECTION,
    safety_rules=SAFETY_RULES,
    urdu_language_rules=URDU_LANGUAGE_RULES,
    follow_up_questions=FOLLOW_UP_QUESTIONS,
    progressive_information_gathering=PROGRESSIVE_INFORMATION_GATHERING,
    proactive_guidance=PROACTIVE_GUIDANCE,
    intent_aware_responses=INTENT_AWARE_RESPONSES,
    concise_voice_responses=CONCISE_VOICE_RESPONSES,
    error_recovery=ERROR_RECOVERY,
    booking_modification_confirmation=BOOKING_MODIFICATION_CONFIRMATION,
    output_format_json=OUTPUT_FORMAT_JSON,
)
