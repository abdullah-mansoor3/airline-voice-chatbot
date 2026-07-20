from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import httpx


class DuffelError(RuntimeError):
    pass


@dataclass(frozen=True)
class DuffelClient:
    """Thin read-only Duffel REST wrapper.

    This client deliberately does not expose order creation/payment methods. The product may
    search flights and read order status, but selected options are stored only in Postgres.
    """

    api_key: str
    base_url: str = "https://api.duffel.com"
    timeout: float = 15.0
    duffel_version: str = "v2"

    @classmethod
    def from_env(cls) -> "DuffelClient":
        api_key = os.getenv("DUFFEL_API_KEY")
        if not api_key:
            raise DuffelError("DUFFEL_API_KEY is required")
        return cls(
            api_key=api_key,
            duffel_version=os.getenv("DUFFEL_VERSION", "v2"),
        )

    async def get_order(self, duffel_order_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/air/orders/{duffel_order_id}")

    async def create_offer_request(
        self,
        *,
        origin: str,
        destination: str,
        departure_date: str,
        adults: int = 1,
        cabin_class: str = "economy",
    ) -> dict[str, Any]:
        payload = {
            "data": {
                "slices": [
                    {
                        "origin": origin,
                        "destination": destination,
                        "departure_date": departure_date,
                    }
                ],
                "passengers": [{"type": "adult"} for _ in range(max(adults, 1))],
                "cabin_class": cabin_class,
            }
        }
        return await self._request("POST", "/air/offer_requests", json=payload)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Duffel-Version": self.duffel_version,
            "Accept": "application/json",
        }
        
        # Log request payload
        print(f"DUFFEL REQUEST: POST {path} payload={json}")

        async with httpx.AsyncClient(
            base_url=self.base_url,
            headers=headers,
            timeout=self.timeout,
        ) as client:
            response = await client.request(method, path, json=json)
            
        if response.status_code >= 400:
            print(f"DUFFEL ERROR RESPONSE: {response.status_code} {response.text}")
            raise DuffelError(
                f"Duffel {method} {path} failed with {response.status_code}: "
                f"{response.text}"
            )
        parsed = response.json()
        return parsed if isinstance(parsed, dict) else {"data": parsed}


async def get_live_order_status(duffel_order_id: str) -> dict[str, Any]:
    response = await DuffelClient.from_env().get_order(duffel_order_id)
    order = response.get("data", {})
    return {
        "duffel_order_id": order.get("id"),
        "status": order.get("status"),
        "booking_reference": order.get("booking_reference"),
        "airline": (order.get("owner") or {}).get("name"),
        "raw": order,
    }
