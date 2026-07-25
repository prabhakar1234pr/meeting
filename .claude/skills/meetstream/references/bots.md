# Bots

The bot is the core object: a MeetStream participant that joins a meeting and
records/transcribes/streams it. This file covers creating bots and every
lifecycle and data-retrieval endpoint.

## Create a bot — `POST /bots/create_bot`

Puts a bot into a meeting immediately, or schedules it for later with `join_at`.

### Required fields

| Field | Type | Description |
|---|---|---|
| `meeting_link` | string | The Google Meet / Zoom / Teams meeting URL |
| `bot_name` | string | Display name the bot shows in the meeting |

### Common optional fields

| Field | Type | Default | Description |
|---|---|---|---|
| `video_required` | boolean | `true` | Record video (composite MP4). Set `false` for audio-only. |
| `bot_image_url` | string | — | Avatar / profile picture URL for the bot |
| `bot_message` | string | — | A chat message the bot posts on joining |
| `join_at` | ISO 8601 string | — | Schedule the bot to join at this time instead of now |
| `callback_url` | string | — | Webhook URL for bot lifecycle events (see `webhooks.md`) |
| `custom_attributes` | object | — | Arbitrary metadata echoed back on the bot and in webhooks |
| `deduplication_key` | string | — | Idempotency key; a repeat returns the original bot |
| `agent_config_id` | string | — | Attach a MIA voice agent (see `mia.md`) |
| `workflow_config_ids` | array | — | Post-meeting workflow triggers |

### Recording & transcription — `recording_config`

`recording_config` controls retention and which transcription provider runs.
Set the transcript provider under `recording_config.transcript.provider`.
Supported providers include: `deepgram`, `assemblyai`, `jigsawstack`, `sarvam`,
`meetstream`, or the meeting's native captions. For **live** streaming
transcription use a streaming provider: `assemblyai_streaming` or
`deepgram_streaming` (paired with `live_transcription_required` — see
`transcription.md`).

### Live media flags (WebSocket streaming)

These enable real-time streaming while the meeting is happening (details and
wire format in `websockets.md`):

| Field | Type | Description |
|---|---|---|
| `live_audio_required` | object `{ "websocket_url": "wss://..." }` | Stream live PCM audio to a WebSocket server you host |
| `live_video_required` | object | Stream live video frames similarly |
| `live_transcription_required` | object `{ "webhook_url": "https://..." }` | POST incremental transcript updates to your webhook |
| `socket_connection_url` | string (wss) | Two-way audio/data bridge — send audio/commands back into the meeting |

### Auto-leave configuration (timeouts, in seconds)

Control when the bot gives up or exits so it doesn't sit in an empty room
forever. All are optional overrides:

| Field | Default | Meaning |
|---|---|---|
| `waiting_room_timeout` | 600 | Give up if not admitted from the waiting room |
| `everyone_left_timeout` | 300 | Leave once everyone else has left |
| `voice_inactivity_timeout` | 100 | Leave after prolonged silence |
| `in_call_recording_timeout` | 14400 | Hard cap on total in-call recording time |
| `recording_permission_denied_timeout` | 60 | (Zoom only) wait for recording permission |

See `guides/features/automatic-leave-configuration.md` for the full behavior.

### Response

```json
{
  "bot_id": "uuid-string",
  "transcript_id": "uuid-string-or-null",
  "meeting_url": "https://...",
  "status": "string"
}
```

Persist `bot_id` (and `transcript_id`) — you need them for every follow-up call.

### Example — join now, audio + transcription + webhooks

```python
import os, requests

BASE = "https://api.meetstream.ai/api/v1"
HEADERS = {
    "Authorization": f"Token {os.environ['MEETSTREAM_API_KEY']}",
    "Content-Type": "application/json",
}

payload = {
    "meeting_link": "https://meet.google.com/abc-defg-hij",
    "bot_name": "Notetaker",
    "video_required": False,
    "callback_url": "https://your-domain.com/webhooks/meetstream",
    "recording_config": {"transcript": {"provider": "deepgram"}},
    "custom_attributes": {"call_id": "c_456"},
    "deduplication_key": "c_456",
}

resp = requests.post(f"{BASE}/bots/create_bot", json=payload, headers=HEADERS)
resp.raise_for_status()
bot = resp.json()
print(bot["bot_id"], bot["transcript_id"])
```

### Example — schedule for later

Add `join_at` in ISO 8601 (include the timezone offset):

```json
{
  "meeting_link": "https://meet.google.com/abc-defg-hij",
  "bot_name": "Notetaker",
  "join_at": "2026-03-24T09:00:00-07:00"
}
```

## Lifecycle & data endpoints

All relative to the base URL; auth header required on every call.

### Track status
- `GET /bots/{bot_id}/status` → `{ "bot_id", "status", "custom_attributes" }`.
  Poll this if you're not using webhooks. Status values track the lifecycle
  (joining, in waiting room, in meeting, recording, stopped, denied, notallowed,
  failed, etc. — the same states webhooks report; see `webhooks.md`).
- `GET /bots/{bot_id}/detail` → rich `bot_details` object: `MeetingLink`,
  `Platform`, `Status`, `StartTime`/`EndTime`, `Duration`, `StatusTimeline`,
  `custom_attributes`, `caption_file.available`, and more.
- `GET /bots` → list/filter bots (see pagination + filters in
  `api-conventions.md`).

### Control
- `GET /bots/{bot_id}/remove_bot` → sends a stop signal; the bot leaves the
  meeting. Returns `{ "message": "Stop signal sent for bot ..." }`.
- Pause / resume recording mid-meeting: `pause-bot-recording` /
  `resume-bot-recording` (resolve exact paths via the docs lookup; see
  `guides/features/pause-resume-recording.md`).

### Retrieve results
- `GET /bots/{bot_id}/get_audio` → `{ "audio_url": "..." }`, a pre-signed S3 URL
  valid ~1 hour. Download promptly.
- `GET /bots/{bot_id}/get_recording_streams` → `{ "video_url": "...",
  "video_info": { "duration", "size_mb", "resolution", "fps", ... } }`. The
  `video_url` is a pre-signed S3 MP4 link valid ~10 minutes.
- `GET /bots/{bot_id}/get_participants` → array of participants with
  `displayName`, `fullName`, `profilePicture`, `humanized_status`, `streamIds`,
  timestamps.
- Transcripts are fetched via the transcript endpoints — see `transcription.md`.
- Speaker timeline, chats, screenshots, per-participant audio/video streams, and
  bot summary each have their own endpoints — resolve exact paths via the docs
  lookup in `api-conventions.md` when needed.

### In-meeting interaction
- `POST /bots/{bot_id}/send_message` — post a chat message from the bot.
  Body: `{ "message": "hello", "metadata": { "message_type": "..." } }`
  (`metadata` optional). Response: `{ "status": "accepted", "bot_id",
  "command": "sendmsg" }`.
- `send-image` posts an image into the chat (resolve path via docs lookup).

### Cleanup
- `delete-bot-data` permanently removes a bot's stored media/transcripts.
- For scheduled bots: `reschedule-bot` and `delete-scheduled-bot` adjust or
  cancel a pending join (resolve paths via docs lookup).
