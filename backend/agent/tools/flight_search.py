from __future__ import annotations

from typing import Any

from .duffel_client import DuffelClient, DuffelError


def _friendly_flight_error(exc: Exception) -> str:
    message = str(exc)
    return f"Flight search failed: {message}"


async def search_alternative_flights(
    *,
    origin: str,
    destination: str,
    departure_date: str,
    adults: int = 1,
    cabin_class: str = "economy",
) -> list[dict[str, Any]]:
    try:
        response = await DuffelClient.from_env().create_offer_request(
            origin=origin,
            destination=destination,
            departure_date=departure_date,
            adults=adults,
            cabin_class=cabin_class,
        )
    except DuffelError as exc:
        return [{"error": _friendly_flight_error(exc)}]
    offer_request = response.get("data", {})
    offers = offer_request.get("offers") or []
    return [_normalize_offer(offer) for offer in offers[:10]]


def _normalize_offer(offer: dict[str, Any]) -> dict[str, Any]:
    slices = offer.get("slices") or []
    first_slice = slices[0] if slices and isinstance(slices[0], dict) else {}
    owner = offer.get("owner") or {}
    return {
        "id": offer.get("id"),
        "airline": owner.get("name") or owner.get("iata_code"),
        "total_amount": offer.get("total_amount"),
        "total_currency": offer.get("total_currency"),
        "expires_at": offer.get("expires_at"),
        "origin": _airport_code(first_slice.get("origin")),
        "destination": _airport_code(first_slice.get("destination")),
        "departure": _first_segment_time(first_slice, "departing_at"),
        "arrival": _first_segment_time(first_slice, "arriving_at", last=True),
        "raw": offer,
    }


def _airport_code(value: Any) -> str | None:
    if isinstance(value, dict):
        return value.get("iata_code") or value.get("id")
    return value if isinstance(value, str) else None


def _first_segment_time(
    slice_payload: dict[str, Any],
    key: str,
    *,
    last: bool = False,
) -> str | None:
    segments = slice_payload.get("segments") or []
    if not segments:
        return None
    segment = segments[-1] if last else segments[0]
    return segment.get(key) if isinstance(segment, dict) else None
