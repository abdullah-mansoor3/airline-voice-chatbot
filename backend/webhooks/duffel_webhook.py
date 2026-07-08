from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/webhooks/duffel", tags=["duffel"])


@router.post("")
async def receive_duffel_webhook():
    raise NotImplementedError("Duffel webhooks start in Phase 6.")
