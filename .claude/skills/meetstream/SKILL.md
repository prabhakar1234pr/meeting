---
name: meetstream
description: >-
  Build integrations on the MeetStream API (MeetStream.ai) — the platform for
  deploying bots into Zoom, Google Meet, and Microsoft Teams meetings to record,
  transcribe, stream live audio/video, post chat messages, run voice agents
  (MIA), and sync calendars. Use this skill whenever the user is working with
  MeetStream, meetstream.ai, or api.meetstream.ai: creating or scheduling a
  meeting bot / recorder / notetaker, fetching a transcript or recording,
  handling MeetStream webhooks and bot events, streaming live meeting audio,
  connecting a calendar, or configuring a MIA agent — even if they don't
  explicitly say "API". Prefer this skill over guessing endpoint paths,
  auth headers, or payload shapes from memory.
---

# MeetStream API

MeetStream.ai is a single API for putting a bot into a Zoom, Google Meet, or
Microsoft Teams meeting. One `create_bot` call with a meeting URL gives you
recording, transcription (post-call and live), participant/speaker metadata,
two-way chat, live audio/video streaming over WebSockets, calendar-driven
scheduling, and voice agents (MIA).

The verified facts below (base URL, auth, endpoint paths, payload shapes) come
straight from the official docs — trust them over your priors. When you need an
endpoint or field that isn't spelled out here, **look it up rather than
guessing** (see "Looking things up" below).

## The three golden rules

1. **Base URL:** `https://api.meetstream.ai/api/v1`
2. **Auth header:** `Authorization: Token YOUR_API_KEY` — the scheme is the
   literal word **`Token`**, a space, then the key. It is **not** `Bearer` and
   **not** `ApiKey`. This is the single most common mistake, so double-check it
   in every request. Keys come from https://app.meetstream.ai/api-key and grant
   full account access — treat them like passwords and read them from an env var
   (e.g. `MEETSTREAM_API_KEY`), never hardcoded.
3. **POST bodies are JSON:** send `Content-Type: application/json`.

## The core workflow

Almost every MeetStream integration is a variation of this loop:

1. **Create a bot** → `POST /bots/create_bot` with the `meeting_link` and a
   `bot_name`. You get back a `bot_id` (and a `transcript_id` if transcription
   is on). To schedule for later, add `join_at` (ISO 8601).
2. **Track it** → either poll `GET /bots/{bot_id}/status`, or (preferred) set a
   `callback_url` at creation and receive webhook events as the bot joins,
   records, and finishes. Webhooks scale better and avoid polling.
3. **Fetch the results** once `bot.done` fires: the transcript
   (`GET /transcript/{transcript_id}/get_transcript`), the recording
   (`GET /bots/{bot_id}/get_recording_streams`), audio, participants, etc.

Recording URLs are **short-lived pre-signed S3 links** (audio ~1 hour, video
~10 minutes) — download them promptly, don't store the URL itself.

## Endpoint quick reference (verified paths)

All paths are relative to the base URL. `{...}` are path params.

| Operation | Method + path |
|---|---|
| Create / schedule bot | `POST /bots/create_bot` |
| List bots (filter/paginate) | `GET /bots` |
| Bot status | `GET /bots/{bot_id}/status` |
| Bot details | `GET /bots/{bot_id}/detail` |
| Make bot leave | `GET /bots/{bot_id}/remove_bot` |
| Participants | `GET /bots/{bot_id}/get_participants` |
| Audio (pre-signed URL) | `GET /bots/{bot_id}/get_audio` |
| Recording / video streams | `GET /bots/{bot_id}/get_recording_streams` |
| Send chat message | `POST /bots/{bot_id}/send_message` |
| Get transcript | `GET /transcript/{transcript_id}/get_transcript` |
| Connect Google Calendar | `POST /calendar/create_calendar` |
| Schedule bot for event | `POST /calendar/schedule/{event_id}` |
| Create MIA agent config | `POST /mia` |

Other documented operations (reschedule, delete scheduled bot, bot summary,
pause/resume recording, speaker timeline, chats, screenshots, send image, delete
bot data, list/re-run transcriptions, calendar sync/recurring/cron, Google
signed-in bots, MIA update/get/delete) exist too — resolve their exact paths via
"Looking things up" before calling them.

## Reference files — read the one that matches the task

Load these on demand; each is a focused, accurate reference for one area:

- **`references/api-conventions.md`** — auth details, errors, pagination,
  idempotency/deduplication keys, custom attributes, and the full doc-lookup
  strategy. Read this first when starting any integration.
- **`references/bots.md`** — the full `create_bot` parameter catalog (recording
  config, transcript providers, auto-leave timeouts, callbacks, live-media
  flags) plus every bot lifecycle and data-retrieval endpoint.
- **`references/webhooks.md`** — the callback event catalog, payload shapes,
  HMAC signature verification, and retry semantics. Read when the user wants to
  receive events instead of polling.
- **`references/transcription.md`** — post-call transcript formats
  (formatted vs. `raw`) and live streaming transcription webhooks.
- **`references/websockets.md`** — live audio/video streaming: the binary PCM
  frame format, decoding, and `socket_connection_url` for two-way audio.
- **`references/calendar.md`** — connecting Google/Outlook calendars and
  auto-scheduling bots for events.
- **`references/mia.md`** — configuring Meeting Intelligence Agents (voice
  agents): pipeline vs. realtime modes, models/voices, and attaching to a bot.

## Looking things up (don't hallucinate)

MeetStream's docs are LLM-friendly. When you're unsure of an exact path,
parameter, or response field, resolve it instead of guessing:

- **Append `.md` to any docs page** for clean markdown, e.g.
  `https://docs.meetstream.ai/api-reference/api-endpoints/bot-endpoints/pause-bot-recording.md`.
- **Full page index:** https://docs.meetstream.ai/llms.txt lists every doc URL.
- **Machine-readable spec:** https://docs.meetstream.ai/openapi.json (or
  `openapi.yaml`) has exact schemas for every endpoint.
- **MCP server:** https://docs.meetstream.ai/_mcp/server — if a MeetStream MCP
  server is connected, query it for live doc context.

If a request fails, first re-check the auth scheme (`Token`, not `Bearer`), the
base URL (`/api/v1`), and that the path matches a documented one.
