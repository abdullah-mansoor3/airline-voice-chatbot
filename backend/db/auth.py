from __future__ import annotations

import os
from dataclasses import dataclass

import httpx


class AuthError(RuntimeError):
    pass


@dataclass(frozen=True)
class AuthenticatedUser:
    id: str
    email: str | None = None


async def verify_supabase_access_token(access_token: str | None) -> AuthenticatedUser:
    if not access_token:
        raise AuthError("Login is required to use the voice agent.")

    supabase_url = _required_env("SUPABASE_URL").rstrip("/")
    publishable_key = _supabase_publishable_key()

    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(
            f"{supabase_url}/auth/v1/user",
            headers={
                "apikey": publishable_key,
                "Authorization": f"Bearer {access_token}",
            },
        )

    if response.status_code != 200:
        raise AuthError("Your login session could not be verified.")

    data = response.json()
    user_id = data.get("id")
    if not user_id:
        raise AuthError("Supabase did not return a user id for this session.")

    return AuthenticatedUser(id=user_id, email=data.get("email"))


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise AuthError(f"{name} is required")
    return value


def _supabase_publishable_key() -> str:
    value = os.getenv("SUPABASE_PUBLISHABLE_KEY") or os.getenv("SUPABASE_KEY")
    if not value:
        raise AuthError("SUPABASE_PUBLISHABLE_KEY is required")
    return value
