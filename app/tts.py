"""ElevenLabs text-to-speech → raw PCM16 bytes.

Uses eleven_flash_v2_5 (low latency) and a raw pcm_* output format so the audio
can go straight onto MeetStream's sendaudio bridge (after resampling to 48k).
"""
from elevenlabs.client import ElevenLabs

from . import config

_client: ElevenLabs | None = None


def _get() -> ElevenLabs:
    global _client
    if _client is None:
        _client = ElevenLabs(api_key=config.ELEVENLABS_API_KEY)
    return _client


def synthesize(text: str) -> bytes:
    """Synthesize `text` to raw PCM16 bytes at ELEVENLABS_OUTPUT_RATE (blocking)."""
    text = (text or "").strip()
    if not text:
        return b""
    audio_iter = _get().text_to_speech.convert(
        voice_id=config.ELEVENLABS_VOICE_ID,
        model_id=config.ELEVENLABS_MODEL,
        text=text,
        output_format=config.ELEVENLABS_OUTPUT_FORMAT,
    )
    return b"".join(audio_iter)
