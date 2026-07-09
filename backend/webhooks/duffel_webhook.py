from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request

from backend.agent.tools.orders import upsert_duffel_order_event

router = APIRouter(prefix="/webhooks/duffel", tags=["duffel"])

SUPPORTED_EVENTS = {
    "order.airline_initiated_change_detected",
    "order_cancellation.created",
    "order.created",
    "order.creation_failed",
    "payment.created",
}


@router.post("")
async def receive_duffel_webhook(
    request: Request,
    x_duffel_webhook_secret: str | None = Header(default=None),
) -> dict[str, Any]:
    expected_secret = os.getenv("DUFFEL_WEBHOOK_SECRET")
    if expected_secret and x_duffel_webhook_secret != expected_secret:
        raise HTTPException(status_code=401, detail="Invalid Duffel webhook secret.")

    payload = await request.json()
    event_type = payload.get("type")
    if event_type not in SUPPORTED_EVENTS:
        return {"status": "ignored", "eventType": event_type}

    saved = await upsert_duffel_order_event(payload)
    return {
        "status": "stored" if saved else "received",
        "eventType": event_type,
        "orderId": saved.get("id") if saved else None,
    }
