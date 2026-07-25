"""MeetStream API client + control-socket command builders.

See the local `meetstream` skill (.claude/skills/meetstream) for the full API
reference. Auth is the literal header `Authorization: Token <key>`.
"""
import base64
import logging

import httpx

from . import config

log = logging.getLogger("teammate.meetstream")

_AUTH = {"Authorization": f"Token {config.MEETSTREAM_API_KEY}"}
_JSON = {**_AUTH, "Content-Type": "application/json"}

# recording_config.transcript.provider must be an OBJECT keyed by the provider
# name, whose value is that provider's config (not a bare string).
_TRANSCRIPT_PROVIDER_CONFIG = {
    "deepgram_streaming": {
        "model": "nova-2",
        "transcription_mode": "sentence",
        "language": "en",
        "punctuate": True,
        "smart_format": True,
        "endpointing": 300,
        "vad_events": True,
        "utterance_end_ms": 1000,
        "encoding": "linear16",
        "channels": 1,
    },
    "assemblyai_streaming": {
        "transcription_mode": "raw",
        "sample_rate": 48000,
        "speech_model": "universal-streaming-english",
        "format_turns": False,
        "encoding": "pcm_s16le",
    },
}


def _transcript_config(provider_name: str) -> dict:
    """Build recording_config.transcript.provider = { <name>: { ...config } }."""
    return {provider_name: _TRANSCRIPT_PROVIDER_CONFIG.get(provider_name, {})}


class MeetStreamError(Exception):
    """Carries MeetStream's HTTP status + response body so callers can see why."""

    def __init__(self, status: int, body: str, payload: dict | None = None):
        self.status = status
        self.body = body
        self.payload = payload
        super().__init__(f"MeetStream {status}: {body}")


async def create_bot(
    meeting_link: str,
    bot_name: str,
    *,
    transcription_webhook: str,
    control_ws_url: str,
    callback_url: str,
    provider: str | None = None,
    custom_attributes: dict | None = None,
) -> dict:
    """Create a bot that joins the meeting with:
    - live streaming transcription posted to `transcription_webhook`
    - a two-way audio bridge at `control_ws_url` (for sendaudio / interrupt)
    - lifecycle events posted to `callback_url`
    """
    payload = {
        "meeting_link": meeting_link,
        "bot_name": bot_name,
        "callback_url": callback_url,
        "live_transcription_required": {"webhook_url": transcription_webhook},
        "socket_connection_url": {"websocket_url": control_ws_url},
        "recording_config": {
            "transcript": {
                "provider": _transcript_config(provider or config.TRANSCRIPT_PROVIDER)
            }
        },
    }
    if custom_attributes:
        payload["custom_attributes"] = custom_attributes
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(
            f"{config.MEETSTREAM_BASE_URL}/bots/create_bot", json=payload, headers=_JSON
        )
        if r.status_code >= 400:
            log.error("create_bot %s: %s\npayload=%s", r.status_code, r.text, payload)
            raise MeetStreamError(r.status_code, r.text, payload)
        return r.json()


async def get_transcript(transcript_id: str, raw: bool = False):
    params = {"raw": "true"} if raw else None
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.get(
            f"{config.MEETSTREAM_BASE_URL}/transcript/{transcript_id}/get_transcript",
            headers=_AUTH,
            params=params,
        )
        r.raise_for_status()
        return r.json()


async def remove_bot(bot_id: str) -> dict:
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(
            f"{config.MEETSTREAM_BASE_URL}/bots/{bot_id}/remove_bot", headers=_AUTH
        )
        r.raise_for_status()
        return r.json()


# ─── Control-socket command builders (JSON text frames) ───────
# These go out over the socket_connection_url that MeetStream opens to us.
def sendaudio_cmd(bot_id: str, pcm_bytes: bytes, sample_rate: int = 48000) -> dict:
    """Make the bot speak. `pcm_bytes` must be raw PCM16 LE mono at sample_rate."""
    return {
        "command": "sendaudio",
        "bot_id": bot_id,
        "audiochunk": base64.b64encode(pcm_bytes).decode("utf-8"),
        "sample_rate": sample_rate,
        "encoding": "pcm16",
        "channels": 1,
        "endianness": "little",
    }


def sendchat_cmd(bot_id: str, text: str, is_final: bool = True, role: str = "assistant") -> dict:
    return {
        "command": "sendchat",
        "bot_id": bot_id,
        "role": role,
        "text": text,
        "is_final": is_final,
    }


def sendmsg_cmd(bot_id: str, message: str) -> dict:
    # Include both `message` and `msg` for cross-platform compatibility.
    return {"command": "sendmsg", "bot_id": bot_id, "message": message, "msg": message}


def interrupt_cmd(bot_id: str) -> dict:
    """Stop current playback and clear the queue (barge-in)."""
    return {"command": "interrupt", "bot_id": bot_id, "action": "clear_audio_queue"}
