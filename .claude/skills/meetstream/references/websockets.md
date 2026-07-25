# WebSockets & live media

MeetStream can stream a meeting's audio (and video) in real time over a
WebSocket **you host**. When the bot joins, MeetStream connects to your endpoint
as a client and pushes frames. There's also a two-way bridge for sending audio/
commands back into the meeting.

## Enabling live audio

Set `live_audio_required` on `create_bot` with your WebSocket URL:

```json
{
  "meeting_url": "https://meet.google.com/abc-defg-hij",
  "live_audio_required": { "websocket_url": "wss://your-server.com/audio" }
}
```

You run the `wss://` server; MeetStream is the client. `live_video_required`
works the same way for video frames. For **sending** audio/data back into the
meeting (e.g. a voice agent speaking), use `socket_connection_url` — see the
two-way section below and `mia.md`.

## Protocol

1. **Connect:** the bot opens the WebSocket to your endpoint on joining.
2. **First message (JSON text frame):**
   ```json
   { "type": "ready", "bot_id": "bot_abc123", "message": "Ready to receive messages" }
   ```
3. **Then:** a continuous stream of **binary** audio frames until the bot exits
   (connection closes with code `1000`).

It's fire-and-forget — your receiver does not acknowledge frames. For forward
compatibility, check byte 0 (message type) and skip frame types you don't
recognize.

## Binary audio frame format

Each frame is length-prefixed with no delimiters:

```
[msg_type:1B][sid_length:2B LE][speaker_id:var][sname_length:2B LE][speaker_name:var][pcm_audio:remaining]
```

| Field | Type | Notes |
|---|---|---|
| `msg_type` | uint8 | Always `0x01` for PCM audio |
| `sid_length` | uint16 **LE** | Byte length of `speaker_id` |
| `speaker_id` | UTF-8 | Platform-specific participant id, stable within a session |
| `sname_length` | uint16 **LE** | Byte length of `speaker_name` |
| `speaker_name` | UTF-8 | Display name |
| `pcm_audio` | int16 LE | Raw PCM samples (all remaining bytes) |

Read each 2-byte little-endian length, then exactly that many bytes for the
string. When attribution is unavailable both fields are `"NoSpeaker"`. The audio
is the **mixed mono** stream of all participants; the speaker fields identify the
*dominant* speaker only.

### Audio properties

| Property | Value |
|---|---|
| Encoding | PCM16 (signed 16-bit) |
| Byte order | Little-endian |
| Sample rate | 48,000 Hz |
| Channels | Mono (1) |

Frame duration: `seconds = (len(pcm_bytes) / 2) / 48000`. Frame sizes vary
(≈1,000 to 50,000+ samples) depending on platform buffering.

### Decoding (Python)

```python
def decode_audio_frame(data: bytes):
    if len(data) < 5 or data[0] != 0x01:
        return None
    sid_len = int.from_bytes(data[1:3], "little")
    speaker_id = data[3:3 + sid_len].decode("utf-8")
    off = 3 + sid_len
    sname_len = int.from_bytes(data[off:off + 2], "little")
    off += 2
    speaker_name = data[off:off + sname_len].decode("utf-8")
    off += sname_len
    return speaker_id, speaker_name, data[off:]  # pcm bytes
```

### Full receiver (Python)

```python
import asyncio, json, websockets

async def receive_audio():
    async with websockets.serve(handler, "0.0.0.0", 8000):
        await asyncio.Future()  # run forever

async def handler(ws):
    async for message in ws:
        if isinstance(message, str):
            hs = json.loads(message)          # the {"type":"ready", ...} frame
            print("bot connected:", hs["bot_id"])
            continue
        decoded = decode_audio_frame(message)
        if not decoded:
            continue
        speaker_id, speaker_name, pcm = decoded
        samples = len(pcm) // 2
        print(f"[{speaker_name}] {samples} samples "
              f"({samples / 48000 * 1000:.0f} ms)")

asyncio.run(receive_audio())
```

Note MeetStream connects **to** your server, so host a WebSocket *server*
(`websockets.serve`), not a client. (The docs also show a `websockets.connect`
client snippet for quick testing against a relay.)

### Working with the PCM

```python
import numpy as np, wave

# to normalized float32 for ML / STT
samples = np.frombuffer(pcm, dtype=np.int16)
floats = samples.astype(np.float32) / 32768.0

# save a WAV
with wave.open("meeting.wav", "wb") as wf:
    wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(48000)
    wf.writeframes(pcm)
```

Many speech-to-text services want 16 kHz — resample the 48 kHz stream down
before sending (linear interpolation via `numpy.interp` is adequate).

## Two-way audio bridge — `socket_connection_url`

Passing `socket_connection_url` (a `wss://` URL) on `create_bot` opens a
bidirectional channel: you receive meeting audio/data **and** can send audio or
control commands back into the meeting. This is what powers MIA voice agents
speaking in the call. Command/control patterns and the bridge server design are
documented at:

- `guides/websockets/meeting-control-patterns.md`
- `guides/websockets/bridge-server-architecture.md`

Resolve exact message schemas there (or via `openapi.json`) rather than guessing.
