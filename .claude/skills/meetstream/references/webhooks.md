# Webhooks & events

Webhooks are the preferred way to track a bot — instead of polling
`GET /bots/{bot_id}/status`, MeetStream POSTs events to a URL you host as the bot
progresses. They scale better and let you react the moment a meeting ends.

## Configuring

Set `callback_url` when creating the bot:

```json
{
  "meeting_link": "https://meet.google.com/abc-defg-hij",
  "bot_name": "Notetaker",
  "video_required": false,
  "callback_url": "https://your-domain.com/webhooks/meetstream"
}
```

Your endpoint must respond with a **2xx** status to acknowledge receipt.
(Live transcription uses a *separate* `webhook_url` under
`live_transcription_required` — see `transcription.md`.)

For local development, MeetStream documents tunneling a local webhook server —
see `guides/webhooks/local-webhook-server.md`.

## Payload shape

Every lifecycle webhook has the same envelope:

```json
{
  "bot_event": "event_name",
  "bot_id": "unique_identifier",
  "bot_status": "status_detail",
  "message": "human_readable_explanation",
  "status_code": 200,
  "timestamp": "ISO-8601_timestamp",
  "custom_attributes": {}
}
```

`custom_attributes` echoes whatever you set on `create_bot`, so you can route
the event to the right record without a separate lookup.

## Event catalog

### Lifecycle (pre-meeting → in-meeting)
- `bot.scheduled` — schedule accepted; bot will join at its scheduled time
- `bot.joining` — bot dispatched toward the meeting
- `bot.in_waiting_room` — bot is waiting to be admitted
- `bot.inmeeting` — bot successfully joined
- `bot.recording` — audio/video capture started
- `bot.recording_permission_allowed` — (Zoom only) host granted recording
- `bot.recording_permission_denied` — (Zoom only) host denied / timed out

### Terminal (exactly one per bot)
- `bot.leaving` — transitional, bot is exiting
- `bot.stopped` — clean exit (`status_code: 200`)
- `bot.kicked` — forcibly removed (`status_code: 200`)
- `bot.denied` — host rejected the join (`status_code: 500`)
- `bot.notallowed` — lobby/waiting-room timeout (`status_code: 500`)
- `bot.failed` — unexpected error (`status_code: 500`)

### Post-call processing (media/transcripts becoming available)
- `audio.processed` — audio extraction complete
- `transcription.processed` — transcript ready to fetch
- `video.processed` — video processing complete
- `bot.done` — pipeline finished; all artifacts ready to retrieve
- `data_deletion` — media removed from storage

**Practical rule:** wait for `bot.done` before fetching the transcript and
recording, since that's when everything is guaranteed ready. If you only need
the transcript, `transcription.processed` is enough.

## Delivery & retry semantics

Webhooks are **best-effort**: a non-2xx response is **not retried**, so make your
handler resilient and idempotent. Guarantees:

- Up to **3** `bot.joining` events may fire (join retry logic).
- Exactly **1** `bot.inmeeting` and exactly **1** terminal event per bot.
- Each post-call event is sent **at most once**.

Because events can arrive more than once (joining) or out of order under
retries, key your handler off `bot_id` + `bot_event` and treat repeats as no-ops.

## Verifying signatures (HMAC)

If you configure a webhook secret, MeetStream signs each request so you can
confirm it's authentic:

- Header: `X-MeetStream-Signature: sha256=<hex_digest>`
- Compute `HMAC-SHA256(secret, raw_request_body)` and compare (constant-time) to
  the hex digest in the header.
- A `X-MeetStream-Timestamp` header supports replay protection — reject requests
  whose timestamp is too old.

Verify against the **raw request body bytes**, before any JSON parsing/
re-serialization, or the digest won't match.

### Example handler (Flask)

```python
import hashlib, hmac, os
from flask import Flask, request, abort

app = Flask(__name__)
SECRET = os.environ["MEETSTREAM_WEBHOOK_SECRET"].encode()

@app.post("/webhooks/meetstream")
def meetstream_webhook():
    raw = request.get_data()  # raw bytes, before parsing
    sent = request.headers.get("X-MeetStream-Signature", "")
    expected = "sha256=" + hmac.new(SECRET, raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sent, expected):
        abort(401)

    event = request.get_json()
    if event["bot_event"] == "bot.done":
        # transcript + recording are ready — fetch them here
        ...
    return "", 200  # any 2xx acknowledges; non-2xx is NOT retried
```
