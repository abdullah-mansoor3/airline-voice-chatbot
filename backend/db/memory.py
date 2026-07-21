from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from groq import Groq

from .conversations import ConversationContext
from .supabase_client import get_service_supabase_client

# ── LLM model used for memory operations ──────────────────────────────────────
_MEMORY_MODEL = "llama-3.1-8b-instant"   # fast, cheap, runs in <1s


@dataclass(frozen=True)
class MemoryContext:
    short_term_summary: str | None = None
    recent_messages: list[dict[str, str | None]] = field(default_factory=list)
    long_term_facts: list[dict[str, str | float | None]] = field(default_factory=list)
    user_profile: dict[str, str | None] = field(default_factory=dict)

    def for_prompt(self) -> dict[str, Any]:
        return {
            "user_profile": self.user_profile,
            "long_term_facts": self.long_term_facts[:6],
            "short_term_summary": self.short_term_summary,
            "recent_messages": self.recent_messages[-3:],
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


# ── Load ───────────────────────────────────────────────────────────────────────

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
        pass  # column not yet migrated

    messages = (
        client.table("messages")
        .select("speaker,original_text,english_text,created_at")
        .eq("conversation_id", conversation.id)
        .order("turn_index", desc=True)
        .limit(3)
        .execute()
    )
    recent_messages = list(reversed(messages.data or []))

    memories = (
        client.table("user_memories")
        .select("memory_key,memory_value,confidence,last_seen_at")
        .eq("user_id", conversation.user_id)
        .order("last_seen_at", desc=True)
        .limit(6)
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


# ── Update ─────────────────────────────────────────────────────────────────────

def _update_memory_after_turn_sync(
    conversation: ConversationContext,
    user_text: str,
    user_language: str,
    agent_text: str,
) -> None:
    client = get_service_supabase_client()

    # ── Short-term summary (LLM rolling compression) ──────────────────────────
    try:
        previous = (
            client.table("conversations")
            .select("short_term_summary,turn_counter")
            .eq("id", conversation.id)
            .limit(1)
            .execute()
        )
        previous_summary = ""
        turn_counter = 0
        if previous.data:
            previous_summary = previous.data[0].get("short_term_summary") or ""
            turn_counter = previous.data[0].get("turn_counter") or 0
            
        turn_counter += 1
        update_data = {"turn_counter": turn_counter}

        if turn_counter % 10 == 0:
            new_summary = _llm_compress_summary(
                previous_summary=previous_summary,
                user_text=user_text,
                agent_text=agent_text,
            )
            update_data["short_term_summary"] = new_summary
            update_data["memory_updated_at"] = datetime.now(timezone.utc).isoformat()
            
        client.table("conversations").update(update_data).eq("id", conversation.id).execute()
    except Exception:
        pass  # column not yet migrated

    # ── Long-term facts (LLM extraction) ─────────────────────────────────────
    try:
        extracted = _llm_extract_facts(
            user_text=user_text,
            agent_text=agent_text,
            language=user_language,
        )
        for memory_key, memory_value in extracted:
            _upsert_memory(
                client,
                user_id=conversation.user_id,
                memory_key=memory_key,
                memory_value=memory_value,
            )
    except Exception:
        pass


# ── LLM helpers ───────────────────────────────────────────────────────────────

def _get_groq_client() -> Groq | None:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return None
    return Groq(api_key=api_key)


def _llm_compress_summary(
    *,
    previous_summary: str,
    user_text: str,
    agent_text: str,
) -> str:
    """
    Ask the LLM to compress the rolling short-term summary into a tight
    narrative paragraph (≤300 words). Falls back to simple concatenation
    if the API call fails.
    """
    client = _get_groq_client()
    if client is None:
        return _fallback_roll_summary(previous_summary, user_text=user_text, agent_text=agent_text)

    prompt = (
        "You are a memory manager for an airline dispute assistant. "
        "Your job is to maintain a concise, factual short-term summary of an ongoing conversation.\n\n"
        "PREVIOUS SUMMARY (may be empty):\n"
        f"{previous_summary or '(none)'}\n\n"
        "NEW TURN:\n"
        f"User: {user_text[:600]}\n"
        f"Assistant: {agent_text[:600]}\n\n"
        "Write an updated summary that:\n"
        "- Preserves all key facts from the previous summary (claim type, airline, dates, amounts, references)\n"
        "- Incorporates what was just discussed\n"
        "- Removes redundant or resolved details\n"
        "- Is at most 500 characters, written as compressed points or phrases that retain the short term general direction and semantics of conversation\n"
        "- Contains NO markdown, NO bullet points, NO headers\n"
        "Output only the summary text. Nothing else."
    )

    try:
        response = client.chat.completions.create(
            model=_MEMORY_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=400,
        )
        result = (response.choices[0].message.content or "").strip()
        if result:
            return result
    except Exception:
        pass

    return _fallback_roll_summary(previous_summary, user_text=user_text, agent_text=agent_text)


def _llm_extract_facts(
    *,
    user_text: str,
    agent_text: str,
    language: str,
) -> list[tuple[str, str]]:
    """
    Ask the LLM to extract structured long-term facts from the conversation turn.
    Returns a list of (memory_key, memory_value) pairs.
    Falls back to regex extraction if the API call fails.
    """
    client = _get_groq_client()
    if client is None:
        return _fallback_extract_facts(user_text, language)

    prompt = (
        "You are extracting durable long term memory facts from a single airline helper bot conversation turn.\n\n"
        f"User said: {user_text[:800]}\n"
        f"Assistant said: {agent_text[:400]}\n\n"
        "Extract ONLY facts that are explicitly stated and would be useful to remember for future turns.\n"
        "Return a JSON object where each key is a snake_case fact name and each value is a string.\n"
        "Allowed keys (use only those that apply, skip the rest):\n"
        "  airline, booking_reference, origin_city, destination_city, departure_date,\n"
        "  claim_type, claim_reason, passenger_name, contact_phone, contact_email,\n"
        "  flight_number, ticket_number, travel_class, compensation_requested,\n"
        "  preferred_language, last_detected_language\n\n"
        "Rules:\n"
        "- Only include keys where the value is clearly stated, not inferred\n"
        "- claim_type values: refund | compensation | baggage | delay | cancellation | denied_boarding\n"
        "- preferred_language / last_detected_language values: en | ur\n"
        "- If nothing relevant is present, return {}\n"
        "Output ONLY valid JSON. No explanation, no markdown fences."
    )

    try:
        response = client.chat.completions.create(
            model=_MEMORY_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=200,
        )
        raw = (response.choices[0].message.content or "").strip()
        # Strip possible markdown fences
        raw = re.sub(r"^```[a-z]*\n?", "", raw).rstrip("`").strip()
        facts_dict: dict = json.loads(raw)
        if isinstance(facts_dict, dict):
            return [(k.lower(), str(v)) for k, v in facts_dict.items() if v]
    except Exception:
        pass

    return _fallback_extract_facts(user_text, language)


# ── Fallback (regex) helpers ───────────────────────────────────────────────────

def _fallback_roll_summary(previous: str, *, user_text: str, agent_text: str) -> str:
    user_line = _compact_text(user_text, 240)
    agent_line = _compact_text(agent_text, 240)
    new_fact = f"User said: {user_line}. Assistant replied: {agent_line}."
    if not previous:
        return new_fact[:1800]
    combined = f"{previous}\n{new_fact}"
    lines = [line for line in combined.splitlines() if line.strip()]
    return "\n".join(lines[-8:])[:1800]


def _fallback_extract_facts(text: str, language: str) -> list[tuple[str, str]]:
    facts: list[tuple[str, str]] = []
    lowered = text.lower()

    if language in {"en", "ur"}:
        facts.append(("last_detected_language", language))

    if any(t in lowered for t in ["pia", "pakistan international airlines"]) or any(
        t in text for t in ["پی آئی اے", "پاکستان انٹرنیشنل"]
    ):
        facts.append(("airline", "PIA"))
    if "airblue" in lowered or "air blue" in lowered:
        facts.append(("airline", "AirBlue"))
    if "serene" in lowered:
        facts.append(("airline", "Serene Air"))

    ref = re.search(r"\b([A-Z0-9]{6})\b", text.upper())
    if ref:
        facts.append(("booking_reference", ref.group(1)))

    if any(t in lowered for t in ["refund", "reimbursement"]) or any(
        t in text for t in ["ریفنڈ", "واپسی"]
    ):
        facts.append(("claim_type", "refund"))
    if any(t in lowered for t in ["cancel", "cancelled", "canceled"]) or "منسوخ" in text:
        facts.append(("claim_reason", "flight_cancelled"))
    if "delay" in lowered or "تاخیر" in text:
        facts.append(("claim_reason", "flight_delayed"))
    if any(t in lowered for t in ["baggage", "luggage"]) or "سامان" in text:
        facts.append(("claim_reason", "baggage"))

    return facts


# ── DB helpers ─────────────────────────────────────────────────────────────────

def _upsert_memory(client, *, user_id: str, memory_key: str, memory_value: str) -> None:
    client.table("user_memories").upsert(
        {
            "user_id": user_id,
            "memory_key": memory_key,
            "memory_value": memory_value,
            "confidence": 0.85,
            "last_seen_at": datetime.now(timezone.utc).isoformat(),
        },
        on_conflict="user_id,memory_key",
    ).execute()


def _compact_text(text: str, limit: int) -> str:
    compact = " ".join((text or "").split())
    if len(compact) <= limit:
        return compact
    return f"{compact[: limit - 3]}..."
