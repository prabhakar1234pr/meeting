"""Redis Streams — the transcription bus.

One stream per meeting (`transcript:{bot_id}`) carries live transcript segments.
Any number of services (responder, notetaker, and future fact-checker / Slack /
calendar consumers) read from it independently via consumer groups.
"""
import json

import redis.asyncio as aioredis

from . import config

_redis: aioredis.Redis | None = None


def get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(config.REDIS_URL, decode_responses=True)
    return _redis


async def publish_transcript(bot_id: str, segment: dict) -> None:
    """Append one transcript segment to this meeting's stream."""
    r = get_redis()
    await r.xadd(
        config.transcript_stream(bot_id),
        {"data": json.dumps(segment)},
        maxlen=10000,
        approximate=True,
    )


async def ensure_group(bot_id: str, group: str) -> None:
    """Create a consumer group at the stream tail (idempotent)."""
    r = get_redis()
    try:
        await r.xgroup_create(
            config.transcript_stream(bot_id), group, id="$", mkstream=True
        )
    except aioredis.ResponseError as e:
        if "BUSYGROUP" not in str(e):
            raise


async def read_group(bot_id: str, group: str, consumer: str, block_ms=2000, count=10):
    """Read new segments for a consumer group. Yields (msg_id, segment dict)."""
    r = get_redis()
    resp = await r.xreadgroup(
        group, consumer, {config.transcript_stream(bot_id): ">"},
        count=count, block=block_ms,
    )
    if not resp:
        return []
    _, entries = resp[0]
    out = []
    for msg_id, fields in entries:
        try:
            out.append((msg_id, json.loads(fields["data"])))
        except (KeyError, json.JSONDecodeError):
            continue
    return out


async def ack(bot_id: str, group: str, msg_id: str) -> None:
    r = get_redis()
    await r.xack(config.transcript_stream(bot_id), group, msg_id)


async def read_all(bot_id: str) -> list[dict]:
    """Return every transcript segment captured for a meeting (whole stream)."""
    r = get_redis()
    entries = await r.xrange(config.transcript_stream(bot_id))
    out = []
    for _msg_id, fields in entries:
        try:
            out.append(json.loads(fields["data"]))
        except (KeyError, json.JSONDecodeError):
            continue
    return out


async def close() -> None:
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None
