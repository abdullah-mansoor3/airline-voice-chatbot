from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator


class AgentOutput(BaseModel):
    answer_markdown: str = Field(min_length=1, max_length=3000)
    language: str
    cited_chunk_ids: list[str] = Field(default_factory=list, max_length=8)
    confidence: float = Field(ge=0.0, le=1.0)
    needs_escalation: bool = False

    @field_validator("language")
    @classmethod
    def supported_language(cls, value: str) -> str:
        if value not in {"en", "ur"}:
            raise ValueError("language must be en or ur")
        return value


@dataclass(frozen=True)
class ValidationResult:
    output: AgentOutput
    warnings: list[str]


PROMPT_INJECTION_PATTERNS = [
    r"ignore\s+(all\s+)?previous\s+instructions",
    r"disregard\s+(the\s+)?system",
    r"you\s+are\s+now",
    r"developer\s+message",
    r"system\s+prompt",
    r"approve\s+(my\s+)?refund\s+no\s+matter",
    r"do\s+not\s+validate",
    r"skip\s+validation",
    r"bypassing",
    r"forget\s+all",
    r"output\s+the\s+following",
    r"print\s+(the\s+)?previous",
    r"base64",
    r"roleplay",
    r"sudo",
]


def validate_user_input(text: str) -> list[str]:
    lowered = text.lower()
    warnings = []
    for pattern in PROMPT_INJECTION_PATTERNS:
        if re.search(pattern, lowered):
            warnings.append("possible_prompt_injection")
            break
    return warnings


def parse_and_validate_agent_output(
    raw_text: str,
    *,
    expected_language: str,
    retrieved_chunks: list[dict[str, Any]],
    expected_jurisdiction: str | None = None,
    requires_policy_grounding: bool = False,
) -> ValidationResult:
    warnings: list[str] = []
    payload = _extract_json(raw_text)

    # Extract the markdown answer text (everything before the ```json block)
    text_part = re.sub(r"```(?:json)?.*?```", "", raw_text, flags=re.DOTALL | re.IGNORECASE).strip()
    # Fallback if no json block: just take the whole thing and strip {}
    if not text_part:
        text_part = re.sub(r"\{.*?\}", "", raw_text, flags=re.DOTALL).strip()
    if not text_part:
        text_part = raw_text.strip()

    # Remove document IDs and citation markers from markdown text
    # This removes patterns like 【id:chunk】, [id:chunk], or any UUID-like patterns in brackets
    text_part = re.sub(r"【[^】]+】", "", text_part)  # Remove 【...】 citations
    text_part = re.sub(r"\[[\da-fA-F-]+:\d+\]", "", text_part)  # Remove [uuid:chunk] citations
    text_part = re.sub(r"\[[\da-fA-F-]{8}-[\da-fA-F-]{4}-[\da-fA-F-]{4}-[\da-fA-F-]{4}-[\da-fA-F-]{12}\]", "", text_part)  # Remove standalone UUIDs
    text_part = re.sub(r"\s+", " ", text_part).strip()  # Clean up extra whitespace

    payload["answer_markdown"] = text_part
    allowed_ids = {chunk.get("id") for chunk in retrieved_chunks if chunk.get("id")}
    retrieved_by_id = {
        chunk.get("id"): chunk for chunk in retrieved_chunks if chunk.get("id")
    }

    try:
        output = AgentOutput.model_validate(payload)
    except ValidationError:
        warnings.append("invalid_agent_json")
        fallback_text = (
            _fallback_answer(expected_language)
            if requires_policy_grounding
            else text_part or _generic_answer_fallback(expected_language)
        )
        output = AgentOutput(
            answer_markdown=fallback_text,
            language=expected_language,
            cited_chunk_ids=[],
            confidence=0.0 if requires_policy_grounding else 0.5,
            needs_escalation=requires_policy_grounding,
        )

    if output.language != expected_language:
        warnings.append("language_mismatch")
        output = output.model_copy(update={"language": expected_language})

    invalid_citations = [
        chunk_id for chunk_id in output.cited_chunk_ids if chunk_id not in allowed_ids
    ]
    if invalid_citations:
        warnings.append("invalid_citations_removed")
        output = output.model_copy(
            update={
                "cited_chunk_ids": [
                    chunk_id
                    for chunk_id in output.cited_chunk_ids
                    if chunk_id in allowed_ids
                ]
            }
        )

    jurisdiction_mismatches = _jurisdiction_mismatched_citations(
        output.cited_chunk_ids,
        retrieved_by_id=retrieved_by_id,
        expected_jurisdiction=expected_jurisdiction,
    )
    if jurisdiction_mismatches:
        warnings.append("jurisdiction_mismatched_citations_removed")
        output = output.model_copy(
            update={
                "cited_chunk_ids": [
                    chunk_id
                    for chunk_id in output.cited_chunk_ids
                    if chunk_id not in jurisdiction_mismatches
                ],
                "needs_escalation": True,
            }
        )

    if requires_policy_grounding and not output.cited_chunk_ids and retrieved_chunks:
        top_ids = [
            chunk.get("id")
            for chunk in retrieved_chunks[:3]
            if chunk.get("id")
        ]
        if top_ids and output.answer_markdown.strip():
            warnings.append("missing_citations_auto_filled")
            output = output.model_copy(update={"cited_chunk_ids": top_ids[:2]})
        else:
            warnings.append("no_valid_citations_forced_escalation")
            output = output.model_copy(update={"needs_escalation": True})

    forced_escalation = any(
        warning in warnings
        for warning in [
            "invalid_agent_json",
            "jurisdiction_mismatched_citations_removed",
            "no_valid_citations_forced_escalation",
        ]
    )

    if (
        requires_policy_grounding
        and output.confidence < 0.75
        and not output.needs_escalation
    ):
        warnings.append("low_confidence_forced_escalation")
        forced_escalation = True
        output = output.model_copy(update={"needs_escalation": True})

    if forced_escalation and requires_policy_grounding:
        output = output.model_copy(
            update={
                "answer_markdown": _fallback_answer(output.language),
                "needs_escalation": True,
            }
        )

    return ValidationResult(output=output, warnings=warnings)


def _jurisdiction_mismatched_citations(
    cited_chunk_ids: list[str],
    *,
    retrieved_by_id: dict[str, dict[str, Any]],
    expected_jurisdiction: str | None,
) -> set[str]:
    if not expected_jurisdiction:
        return set()
    if expected_jurisdiction == "international":
        allowed = {"international", "web"}
    else:
        allowed = {expected_jurisdiction, "web"}

    mismatched: set[str] = set()
    for chunk_id in cited_chunk_ids:
        chunk = retrieved_by_id.get(chunk_id) or {}
        chunk_jurisdiction = chunk.get("jurisdiction")
        if chunk_jurisdiction and chunk_jurisdiction not in allowed:
            mismatched.add(chunk_id)
    return mismatched


def _extract_json(raw_text: str) -> dict[str, Any]:
    text = raw_text.strip()
    
    # Try to find a JSON block specifically
    block_match = re.search(r"```(?:json)?(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)
    if block_match:
        json_str = block_match.group(1).strip()
        try:
            parsed = json.loads(json_str)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            pass

    # Fallback to finding outermost curly braces
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        return {}
    try:
        parsed = json.loads(match.group(0))
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def _fallback_answer(language: str) -> str:
    if language == "en":
        return (
            "I could not validate a grounded answer for this claim. "
            "Please share the airline, route, date, and what happened so I can check the policy."
        )
    return (
        "میں اس دعوے کے لیے قابلِ اعتماد جواب کی تصدیق نہیں کر سکا۔ "
        "براہ کرم ائیرلائن، روٹ، تاریخ، اور مسئلے کی تفصیل بتائیں تاکہ پالیسی چیک کی جا سکے۔"
    )


def _generic_answer_fallback(language: str) -> str:
    if language == "en":
        return "I could not format a reliable answer right now. Please try again."
    return "میں ابھی قابلِ اعتماد جواب نہیں بنا سکا۔ براہ کرم دوبارہ کوشش کریں۔"
