from __future__ import annotations

import os

from supabase import Client, create_client


def get_public_supabase_client() -> Client:
    url = _required_env("SUPABASE_URL")
    key = _supabase_publishable_key()
    return create_client(url, key)


def get_service_supabase_client() -> Client:
    url = _required_env("SUPABASE_URL")
    key = _required_env("SUPABASE_SECRET_KEY")
    return create_client(url, key)


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def _supabase_publishable_key() -> str:
    value = os.getenv("SUPABASE_PUBLISHABLE_KEY") or os.getenv("SUPABASE_KEY")
    if not value:
        raise RuntimeError("SUPABASE_PUBLISHABLE_KEY is required")
    return value
