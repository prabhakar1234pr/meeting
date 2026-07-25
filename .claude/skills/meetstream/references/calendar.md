# Calendar integration

Connect a Google (or Outlook) calendar so MeetStream can see upcoming events and
automatically dispatch a bot to each meeting — no manual `create_bot` per call.

Flow: **connect a calendar → sync events → schedule a bot for chosen events**
(optionally with recurring/cron automation).

## Connect a Google Calendar — `POST /calendar/create_calendar`

You supply OAuth credentials for the account whose calendar you're connecting.
Body (all required strings):

```json
{
  "google_client_id": "...",
  "google_client_secret": "...",
  "google_refresh_token": "..."
}
```

The `google_refresh_token` comes from an OAuth consent flow you run first — see
`guides/calendar-integrations/google-calendar-oauth-setup.md` for obtaining it.

Response (abridged):

```json
{
  "calendar_id": "...",
  "platform": "google_calendar",
  "user_email": "...",
  "user_name": "...",
  "primary_calendar_id": "...",
  "calendars": [
    { "id": "...", "summary": "...", "isPrimary": true, "accessRole": "owner",
      "timeZone": "America/Los_Angeles", "selected": true }
  ],
  "watch_setup": { "success": true, "watches_setup": 1, "watch_results": [ ... ] }
}
```

`watch_setup` reflects push-notification channels MeetStream registers so it
learns about calendar changes. Store the returned `calendar_id`.

### Example

```python
import os, requests

BASE = "https://api.meetstream.ai/api/v1"
HEADERS = {
    "Authorization": f"Token {os.environ['MEETSTREAM_API_KEY']}",
    "Content-Type": "application/json",
}

resp = requests.post(f"{BASE}/calendar/create_calendar", headers=HEADERS, json={
    "google_client_id": os.environ["GOOGLE_CLIENT_ID"],
    "google_client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
    "google_refresh_token": os.environ["GOOGLE_REFRESH_TOKEN"],
})
resp.raise_for_status()
print(resp.json()["calendar_id"])
```

Outlook is supported via a similar setup —
`guides/calendar-integrations/outlook-calendar-setup.md`.

## Schedule a bot for an event — `POST /calendar/schedule/{event_id}`

Once events are synced, schedule a bot for a specific event by its `event_id`.
**No request body** is required — auth header only.

Response (abridged):

```json
{
  "scheduled": true,
  "schedule_id": "bot-945804c8-0834f3b9-043",
  "bot_id": "0834f3b9-043c-4496-9eb0-e869bd38ca1e",
  "schedule_group": "google_meet",
  "event_id": "evt_2a1179ef047a5285",
  "scheduled_time": "2026-03-24T15:57:00+00:00",
  "is_recurring_occurrence": false,
  "bot_config": {
    "bot_name": "Maddy's Agent",
    "meeting_url": "https://meet.google.com/sfw-fjpa-syk",
    "join_at": "2026-03-24T09:00:00-07:00",
    "audio_required": true,
    "video_required": true,
    "deduplication_key": "001009"
  }
}
```

The response's `bot_config` shows the bot that will be created for the event
(including a `deduplication_key`, so re-scheduling is safe).

## Other calendar endpoints

Resolve exact paths via the docs lookup in `api-conventions.md` when needed:

- **Get calendars** — list connected calendars.
- **Fetch / sync events** — pull the latest events from a connected calendar.
- **Remove scheduled event** — cancel a scheduled bot for an event.
- **Toggle recurring event** — enable/disable auto-scheduling across a recurring
  series.
- **Setup cron / disable cron** — automate periodic sync + scheduling so new
  meetings get bots without manual calls.
- **Disconnect calendar** — remove the connection and its watches.
