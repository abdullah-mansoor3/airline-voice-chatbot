from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class VadSettings:
    silence_ms: int = 900
    min_speech_ms: int = 300
    energy_threshold: float = 0.015


DEFAULT_VAD_SETTINGS = VadSettings()
