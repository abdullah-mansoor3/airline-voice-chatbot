from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone

from .supabase_client import get_service_supabase_client


class ConversationHistoryError(RuntimeError):
    pass


@dataclass(frozen=True)
class ConversationContext:
    id: str
    user_id: str


async def get_conversation(
    *, user_id: str, conversation_id: str
) -> ConversationContext:
    return await asyncio.to_thread(
        _get_conversation_sync,
        user_id,
        conversation_id,
    )

async def create_conversation(
    *, user_id: str, title: str = "New conversation"
) -> ConversationContext:
    return await asyncio.to_thread(
        _create_conversation_sync,
        user_id,
        title,
    )


async def record_turn(
    *,
    conversation: ConversationContext,
    user_text: str,
    user_language: str,
    user_english_text: str | None = None,
    agent_text: str,
    agent_language: str,
) -> None:
    """Write a completed turn (user + agent messages) to the ``messages`` table.

    ``user_english_text`` is the output of the parallel Whisper translate call
    (Phase 2).  When the user spoke English it will be ``None`` because the
    transcription is already in English.
    """
    await asyncio.to_thread(
        _record_turn_sync,
        conversation,
        user_text,
        user_language,
        user_english_text,
        agent_text,
        agent_language,
    )


def _get_conversation_sync(
    user_id: str, conversation_id: str
) -> ConversationContext:
    client = get_service_supabase_client()
    response = (
        client.table("conversations")
        .select("id,user_id")
        .eq("id", conversation_id)
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )
    if response.data:
        return ConversationContext(id=response.data[0]["id"], user_id=user_id)
    raise ConversationHistoryError("Conversation was not found for this user.")


def _create_conversation_sync(user_id: str, title: str) -> ConversationContext:
    client = get_service_supabase_client()
    response = (
        client.table("conversations")
        .insert(
            {
                "user_id": user_id,
                "title": _unique_conversation_title(client, user_id, title),
                "status": "active",
                "primary_language": "ur",
            }
        )
        .execute()
    )
    if not response.data:
        raise ConversationHistoryError("Could not create conversation history.")

    return ConversationContext(id=response.data[0]["id"], user_id=user_id)


def _record_turn_sync(
    conversation: ConversationContext,
    user_text: str,
    user_language: str,
    user_english_text: str | None,
    agent_text: str,
    agent_language: str,
) -> None:
    client = get_service_supabase_client()
    turn_index = _next_turn_index(client, conversation.id)

    # If language is English the text IS already English, no separate column needed.
    # If the caller passed a translation (Phase 2 parallel translate) use it;
    # otherwise fall back to the old heuristic.
    effective_user_english = (
        user_english_text
        if user_english_text is not None
        else (user_text if user_language == "en" else None)
    )
    agent_english_text = agent_text if agent_language == "en" else None

    client.table("messages").insert(
        [
            {
                "conversation_id": conversation.id,
                "turn_index": turn_index,
                "speaker": "user",
                "original_text": user_text,
                "english_text": effective_user_english,
            },
            {
                "conversation_id": conversation.id,
                "turn_index": turn_index + 1,
                "speaker": "agent",
                "original_text": agent_text,
                "english_text": agent_english_text,
            },
        ]
    ).execute()

    update_payload = {
        "last_message_at": datetime.now(timezone.utc).isoformat(),
        "primary_language": user_language,
    }
    if turn_index == 0 and user_text:
        update_payload["title"] = _unique_conversation_title(
            client,
            conversation.user_id,
            user_text[:80],
            exclude_conversation_id=conversation.id,
        )

    client.table("conversations").update(update_payload).eq(
        "id", conversation.id
    ).execute()


def _next_turn_index(client, conversation_id: str) -> int:
    response = (
        client.table("messages")
        .select("turn_index")
        .eq("conversation_id", conversation_id)
        .order("turn_index", desc=True)
        .limit(1)
        .execute()
    )
    if not response.data:
        return 0
    return int(response.data[0]["turn_index"]) + 1


def _unique_conversation_title(
    client,
    user_id: str,
    base_title: str,
    *,
    exclude_conversation_id: str | None = None,
) -> str:
    base = (base_title.strip() or "New conversation")[:80]
    response = (
        client.table("conversations")
        .select("id,title")
        .eq("user_id", user_id)
        .execute()
    )
    existing = {
        row["title"]
        for row in (response.data or [])
        if row.get("title") and row.get("id") != exclude_conversation_id
    }
    if base not in existing:
        return base

    counter = 2
    while True:
        suffix = f" ({counter})"
        candidate = f"{base[:80 - len(suffix)]}{suffix}"
        if candidate not in existing:
            return candidate
        counter += 1
