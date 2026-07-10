from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .conversations import ConversationContext
from .supabase_client import get_service_supabase_client


@dataclass(frozen=True)
class MemoryContext:
    short_term_summary: str | None = None
    recent_messages: list[dict[str, str | None]] = field(default_factory=list)
    long_term_facts: list[dict[str, str | float | None]] = field(default_factory=list)
    user_profile: dict[str, str | None] = field(default_factory=dict)

    def for_prompt(self) -> dict[str, Any]:
        return {
            "user_profile": self.user_profile,
            "long_term_facts": self.long_term_facts[:12],
            "short_term_summary": self.short_term_summary,
            "recent_messages": self.recent_messages[-6:],
        }


async def load_memory_context(conversation: ConversationContext) -> MemoryContext:
    return await asyncio.to_thread(_load_memory_context_sync, conversation)


async def update_memory_after_turn(
    *,
    conversation: ConversationContext,
    user_text: str,
    user_language: str,
    agent_text: str,
) -> None:
    await asyncio.to_thread(
        _update_memory_after_turn_sync,
        conversation,
        user_text,
        user_language,
        agent_text,
    )


def _load_memory_context_sync(conversation: ConversationContext) -> MemoryContext:
    client = get_service_supabase_client()

    short_summary = None
    try:
        convo = (
            client.table("conversations")
            .select("short_term_summary")
            .eq("id", conversation.id)
            .limit(1)
            .execute()
        )
        if convo.data:
            short_summary = convo.data[0].get("short_term_summary")
    except Exception:
        pass  # Column likely doesn't exist yet

    messages = (
        client.table("messages")
        .select("speaker,original_text,english_text,created_at")
        .eq("conversation_id", conversation.id)
        .order("turn_index", desc=True)
        .limit(6)
        .execute()
    )
    recent_messages = list(reversed(messages.data or []))

    memories = (
        client.table("user_memories")
        .select("memory_key,memory_value,confidence,last_seen_at")
        .eq("user_id", conversation.user_id)
        .order("last_seen_at", desc=True)
        .limit(12)
        .execute()
    )

    user = (
        client.table("users")
        .select("full_name,phone,preferred_language")
        .eq("id", conversation.user_id)
        .limit(1)
        .execute()
    )
    profile = user.data[0] if user.data else {}

    return MemoryContext(
        short_term_summary=short_summary,
        recent_messages=recent_messages,
        long_term_facts=memories.data or [],
        user_profile=profile,
    )


def _update_memory_after_turn_sync(
    conversation: ConversationContext,
    user_text: str,
    user_language: str,
    agent_text: str,
) -> None:
    client = get_service_supabase_client()
    try:
        previous = (
            client.table("conversations")
            .select("short_term_summary")
            .eq("id", conversation.id)
            .limit(1)
            .execute()
        )
        previous_summary = ""
        if previous.data:
            previous_summary = previous.data[0].get("short_term_summary") or ""

        summary = _roll_summary(previous_summary, user_text=user_text, agent_text=agent_text)
        client.table("conversations").update(
            {
                "short_term_summary": summary,
                "memory_updated_at": datetime.now(timezone.utc).isoformat(),
            }
        ).eq("id", conversation.id).execute()
    except Exception:
        pass  # Column likely doesn't exist yet

    for memory_key, memory_value in _extract_long_term_facts(user_text, user_language):
        _upsert_memory(
            client,
            user_id=conversation.user_id,
            memory_key=memory_key,
            memory_value=memory_value,
        )


def _roll_summary(previous: str, *, user_text: str, agent_text: str) -> str:
    user_line = _compact_text(user_text, 240)
    agent_line = _compact_text(agent_text, 240)
    new_fact = f"Latest turn: user said `{user_line}`. Assistant responded `{agent_line}`."
    if not previous:
        return new_fact[:1800]
    combined = f"{previous}\n{new_fact}"
    lines = [line for line in combined.splitlines() if line.strip()]
    return "\n".join(lines[-8:])[:1800]


def _extract_long_term_facts(text: str, language: str) -> list[tuple[str, str]]:
    facts: list[tuple[str, str]] = []
    lowered = text.lower()

    if language in {"en", "ur"}:
        facts.append(("last_detected_language", language))

    if any(term in lowered for term in ["pia", "pakistan international airlines"]) or any(
        term in text for term in ["پی آئی اے", "پی آئی اے", "پاکستان انٹرنیشنل"]
    ):
        facts.append(("airline", "PIA"))
    if "airblue" in lowered or "air blue" in lowered or "ائربلو" in text:
        facts.append(("airline", "AirBlue"))
    if "serene" in lowered or "سیرین" in text:
        facts.append(("airline", "Serene Air"))

    booking_match = re.search(r"\b([A-Z0-9]{6})\b", text.upper())
    if booking_match:
        facts.append(("booking_reference", booking_match.group(1)))

    if any(term in lowered for term in ["refund", "reimbursement"]) or any(
        term in text for term in ["ریفنڈ", "واپسی"]
    ):
        facts.append(("claim_type", "refund"))
    if any(term in lowered for term in ["cancel", "cancelled", "canceled"]) or any(
        term in text for term in ["منسوخ", "کینسل"]
    ):
        facts.append(("claim_reason", "flight_cancelled"))
    if "delay" in lowered or "تاخیر" in text:
        facts.append(("claim_reason", "flight_delayed"))
    if any(term in lowered for term in ["baggage", "luggage"]) or "سامان" in text:
        facts.append(("claim_reason", "baggage"))

    return facts


def _upsert_memory(client, *, user_id: str, memory_key: str, memory_value: str) -> None:
    client.table("user_memories").upsert(
        {
            "user_id": user_id,
            "memory_key": memory_key,
            "memory_value": memory_value,
            "confidence": 0.8,
            "last_seen_at": datetime.now(timezone.utc).isoformat(),
        },
        on_conflict="user_id,memory_key",
    ).execute()


def _compact_text(text: str, limit: int) -> str:
    compact = " ".join((text or "").split())
    if len(compact) <= limit:
        return compact
    return f"{compact[: limit - 3]}..."
