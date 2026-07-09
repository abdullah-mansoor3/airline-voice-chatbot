from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from backend.db.supabase_client import get_service_supabase_client


class OrderToolError(RuntimeError):
    pass


async def get_order(
    *,
    user_id: str,
    booking_reference: str | None = None,
    duffel_order_id: str | None = None,
    order_id: str | None = None,
) -> dict[str, Any] | None:
    return await asyncio.to_thread(
        _get_order_sync,
        user_id=user_id,
        booking_reference=booking_reference,
        duffel_order_id=duffel_order_id,
        order_id=order_id,
    )


async def save_local_order(
    *,
    user_id: str,
    order_type: str,
    status: str,
    duffel_order_id: str | None = None,
    booking_reference: str | None = None,
    airline: str | None = None,
    origin: str | None = None,
    destination: str | None = None,
    departure_date: str | None = None,
    amount: float | None = None,
    fare_class: str | None = None,
    raw_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return await asyncio.to_thread(
        _save_local_order_sync,
        user_id=user_id,
        order_type=order_type,
        status=status,
        duffel_order_id=duffel_order_id,
        booking_reference=booking_reference,
        airline=airline,
        origin=origin,
        destination=destination,
        departure_date=departure_date,
        amount=amount,
        fare_class=fare_class,
        raw_payload=raw_payload,
    )


async def upsert_duffel_order_event(payload: dict[str, Any]) -> dict[str, Any] | None:
    return await asyncio.to_thread(_upsert_duffel_order_event_sync, payload)


def _get_order_sync(
    *,
    user_id: str,
    booking_reference: str | None,
    duffel_order_id: str | None,
    order_id: str | None,
) -> dict[str, Any] | None:
    if not any([booking_reference, duffel_order_id, order_id]):
        raise OrderToolError("One order identifier is required.")

    query = (
        get_service_supabase_client()
        .table("orders")
        .select("*")
        .eq("user_id", user_id)
        .limit(1)
    )
    if order_id:
        query = query.eq("id", order_id)
    if duffel_order_id:
        query = query.eq("duffel_order_id", duffel_order_id)
    if booking_reference:
        query = query.eq("booking_reference", booking_reference.upper())

    response = query.execute()
    return response.data[0] if response.data else None


def _save_local_order_sync(
    *,
    user_id: str,
    order_type: str,
    status: str,
    duffel_order_id: str | None,
    booking_reference: str | None,
    airline: str | None,
    origin: str | None,
    destination: str | None,
    departure_date: str | None,
    amount: float | None,
    fare_class: str | None,
    raw_payload: dict[str, Any] | None,
) -> dict[str, Any]:
    payload = {
        "user_id": user_id,
        "duffel_order_id": duffel_order_id,
        "booking_reference": booking_reference.upper() if booking_reference else None,
        "airline": airline,
        "origin": origin,
        "destination": destination,
        "departure_date": departure_date,
        "order_type": order_type,
        "amount": amount,
        "fare_class": fare_class,
        "status": status,
        "raw_payload": raw_payload,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    response = get_service_supabase_client().table("orders").insert(payload).execute()
    if not response.data:
        raise OrderToolError("Could not save local order.")
    return response.data[0]


def _upsert_duffel_order_event_sync(payload: dict[str, Any]) -> dict[str, Any] | None:
    event_data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    order = _extract_order_data(event_data)
    if not order.get("duffel_order_id"):
        return None

    response = (
        get_service_supabase_client()
        .table("orders")
        .upsert(order, on_conflict="duffel_order_id")
        .execute()
    )
    return response.data[0] if response.data else None


def _extract_order_data(data: dict[str, Any]) -> dict[str, Any]:
    order = data.get("object") if isinstance(data.get("object"), dict) else data
    duffel_order_id = order.get("id")
    booking_reference = (
        order.get("booking_reference")
        or order.get("airline_initiated_change", {}).get("booking_reference")
    )
    airline = _first_airline_name(order)
    slices = order.get("slices") or []
    first_slice = slices[0] if slices and isinstance(slices[0], dict) else {}

    return {
        "duffel_order_id": duffel_order_id,
        "booking_reference": booking_reference,
        "airline": airline,
        "origin": _airport_code(first_slice.get("origin")),
        "destination": _airport_code(first_slice.get("destination")),
        "departure_date": _departure_date(first_slice),
        "order_type": "duffel_order",
        "amount": _amount(order),
        "fare_class": _fare_class(order),
        "status": order.get("status") or data.get("type"),
        "raw_payload": data,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def _first_airline_name(order: dict[str, Any]) -> str | None:
    owner = order.get("owner")
    if isinstance(owner, dict):
        return owner.get("name") or owner.get("iata_code")
    return None


def _airport_code(value: Any) -> str | None:
    if isinstance(value, dict):
        return value.get("iata_code") or value.get("id")
    return value if isinstance(value, str) else None


def _departure_date(slice_payload: dict[str, Any]) -> str | None:
    segments = slice_payload.get("segments") or []
    first_segment = segments[0] if segments and isinstance(segments[0], dict) else {}
    departing_at = first_segment.get("departing_at")
    if isinstance(departing_at, str) and len(departing_at) >= 10:
        return departing_at[:10]
    return slice_payload.get("departure_date")


def _amount(order: dict[str, Any]) -> float | None:
    value = order.get("total_amount") or order.get("base_amount")
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _fare_class(order: dict[str, Any]) -> str | None:
    slices = order.get("slices") or []
    first_slice = slices[0] if slices and isinstance(slices[0], dict) else {}
    segments = first_slice.get("segments") or []
    first_segment = segments[0] if segments and isinstance(segments[0], dict) else {}
    passengers = first_segment.get("passengers") or []
    first_passenger = passengers[0] if passengers and isinstance(passengers[0], dict) else {}
    return first_passenger.get("cabin_class") or first_passenger.get("fare_basis_code")
