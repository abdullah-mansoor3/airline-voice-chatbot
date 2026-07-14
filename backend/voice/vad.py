from __future__ import annotations

"""Voice Activity Detection helpers.

LEGACY: This module is no longer used in production. The frontend now uses
Silero VAD (@ricky0123/vad-web) for end-of-turn detection and barge-in,
replacing the RMS-threshold approach. The server-side VAD logic here is
retained only for reference or potential future server-side VAD integration.

The primary end-of-turn signal now comes from the client (a ``"stop"`` event
after the browser-side Silero VAD fires). Barge-in detection is also
client-driven: the browser sends a ``"cancel"`` event when it detects the
user speaking during TTS playback. The server cancels in-flight TTS work
when it receives that event (see ``server.py``).

Note: The energy_threshold and related constants here are no longer used by
the frontend and do not need to match any client-side configuration.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class VadSettings:
    silence_ms: int = 900
    """Milliseconds of silence before end-of-turn fires on the client."""
    min_speech_ms: int = 300
    """Minimum speech duration required before an utterance is submitted."""
    energy_threshold: float = 0.015
    """RMS energy threshold above which a frame is considered 'speech'.
    Used by the browser-side VAD and optionally for server-side gating."""
    barge_in_energy_threshold: float = 0.020
    """Energy threshold for detecting barge-in during TTS playback.
    Slightly higher than speech threshold to avoid false positives from
    TTS audio leaking into the microphone."""


DEFAULT_VAD_SETTINGS = VadSettings()


def is_speech_frame(pcm_frame: bytes, *, settings: VadSettings = DEFAULT_VAD_SETTINGS) -> bool:
    """Return True if the PCM frame's RMS energy exceeds the speech threshold.

    Expects 16-bit little-endian mono PCM (the format produced by Web Audio API
    ``ScriptProcessorNode`` / ``AudioWorkletProcessor`` when resampled to 16 kHz).
    Returns False for empty or malformed frames rather than raising.
    """
    if not pcm_frame or len(pcm_frame) < 2:
        return False

    import struct

    n_samples = len(pcm_frame) // 2
    try:
        samples = struct.unpack(f"<{n_samples}h", pcm_frame[: n_samples * 2])
    except struct.error:
        return False

    rms = (sum(s * s for s in samples) / n_samples) ** 0.5 / 32768.0
    return rms >= settings.energy_threshold
