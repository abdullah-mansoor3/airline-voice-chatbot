from __future__ import annotations

import edge_tts


class TextToSpeechError(RuntimeError):
    pass


VOICE_BY_LANGUAGE = {
    "ur": "ur-PK-UzmaNeural",
    "en": "en-US-AriaNeural",
}


async def synthesize_speech(text: str, language: str) -> bytes:
    voice = VOICE_BY_LANGUAGE.get(language[:2], VOICE_BY_LANGUAGE["en"])
    communicate = edge_tts.Communicate(text=text, voice=voice)
    audio = bytearray()

    try:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio.extend(chunk["data"])
    except Exception as exc:
        raise TextToSpeechError(f"edge-tts failed: {exc}") from exc

    if not audio:
        raise TextToSpeechError("edge-tts returned no audio.")

    return bytes(audio)
