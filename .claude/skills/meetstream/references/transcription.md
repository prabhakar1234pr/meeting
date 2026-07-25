# Transcription

MeetStream offers two transcription modes: **post-call** (fetch a full transcript
after the meeting) and **live** (incremental updates streamed to a webhook during
the meeting).

## Choosing a provider

The transcript provider is set on `create_bot` under
`recording_config.transcript.provider`:

- **Post-call / batch:** `deepgram`, `assemblyai`, `jigsawstack`, `sarvam`,
  `meetstream`, or the meeting's native captions.
- **Live streaming:** `assemblyai_streaming` or `deepgram_streaming` (required
  when you also set `live_transcription_required`).

## Post-call transcript — `GET /transcript/{transcript_id}/get_transcript`

Use the `transcript_id` returned by `create_bot` (also available on the bot).
Fetch it after `transcription.processed` / `bot.done` fires.

Query param: `raw` (boolean). Omit or `false` for MeetStream's normalized
format; `true` for the underlying provider's raw output.

### Formatted response (default)

An array of speaker-attributed segments:

```json
[
  {
    "speaker": "Alice",
    "transcript": "Let's get started.",
    "start_time": 12.4,
    "end_time": 14.1,
    "absolute_start_time": "2026-03-24T16:00:12.400Z",
    "absolute_end_time": "2026-03-24T16:00:14.100Z",
    "words": [
      {
        "word": "lets",
        "punctuated_word": "Let's",
        "start": 12.4,
        "end": 12.7,
        "confidence": 0.99,
        "speaker": 0,
        "speaker_confidence": 0.98
      }
    ]
  }
]
```

`start_time`/`end_time` are seconds from the meeting start; `absolute_*` are
wall-clock ISO 8601. Each segment carries word-level timing/confidence.

### Raw response (`raw=true`)

The provider's own object: `{ id, status, text, language_code, audio_url,
confidence, audio_duration, speaker_labels, words: [...], utterances: [...] }`.
Field names follow the provider, so prefer the formatted shape unless you
specifically need provider-native data.

### Example

```python
import os, requests

BASE = "https://api.meetstream.ai/api/v1"
HEADERS = {"Authorization": f"Token {os.environ['MEETSTREAM_API_KEY']}"}

r = requests.get(f"{BASE}/transcript/{transcript_id}/get_transcript",
                 headers=HEADERS)
r.raise_for_status()
for seg in r.json():
    print(f"{seg['speaker']}: {seg['transcript']}")
```

### Related endpoints
- List a bot's transcriptions and re-transcribe existing audio are separate
  endpoints (`get-bot-transcriptions`, `transcribe-bot-audio`) — resolve exact
  paths via the docs lookup in `api-conventions.md`.

## Live streaming transcription

Get incremental transcript updates during the meeting via a webhook.

### Configure on `create_bot`

```json
{
  "live_transcription_required": { "webhook_url": "https://your-domain.com/webhook" },
  "recording_config": { "transcript": { "provider": "deepgram_streaming" } }
}
```

Pair `live_transcription_required` with a **streaming** provider
(`assemblyai_streaming` or `deepgram_streaming`).

### Webhook payload

MeetStream POSTs updates as speech happens:

```json
{
  "bot_id": "8ceabf49-d392-4c04-8e91-bd9601a0df6e",
  "speakerName": "Madan Raj",
  "timestamp": "2026-01-24T17:00:30.354452",
  "new_text": "hear",
  "transcript": "can you hear",
  "words": [
    {
      "word": "hear",
      "punctuated_word": "hear",
      "start": 2.0,
      "end": 2.08,
      "confidence": 0.999955,
      "speaker": "Madan Raj",
      "word_is_final": false
    }
  ],
  "end_of_turn": false,
  "custom_attributes": {}
}
```

Key fields:
- `new_text` — the incremental chunk just recognized.
- `transcript` — the current buffer (may be partial or complete).
- `word_is_final` — `false` for interim guesses, `true` once committed. Render
  interim text as tentative and replace it when finals arrive.
- `end_of_turn` — `true` when the speaker's phrase/segment is complete; a good
  point to flush a finalized line.

This is distinct from the lifecycle `callback_url` in `webhooks.md` — live
transcription uses its own `webhook_url` and its own payload shape.
