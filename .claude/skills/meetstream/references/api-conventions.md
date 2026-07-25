# MeetStream API conventions

Shared conventions that apply across every MeetStream endpoint. Read this before
starting a new integration.

## Base URL

```
https://api.meetstream.ai/api/v1
```

Every path in this skill is relative to this base. Note the `/api/v1` — leaving
it off is a common 404 cause.

## Authentication

Every request carries your API key in the `Authorization` header:

```
Authorization: Token YOUR_API_KEY
```

The scheme is the literal word **`Token`** followed by a space and the key —
**not** `Bearer`, **not** `ApiKey`. This is the most frequent integration bug,
so verify it in every request.

Get a key from the dashboard at https://app.meetstream.ai/api-key (API tab). The
key grants full access to your account's bots and data, so treat it like a
password: load it from an environment variable, never commit it.

### Minimal examples

curl:

```bash
curl https://api.meetstream.ai/api/v1/bots \
  -H "Authorization: Token $MEETSTREAM_API_KEY"
```

Python (`requests`):

```python
import os, requests

BASE = "https://api.meetstream.ai/api/v1"
HEADERS = {"Authorization": f"Token {os.environ['MEETSTREAM_API_KEY']}"}

resp = requests.get(f"{BASE}/bots", headers=HEADERS)
resp.raise_for_status()
print(resp.json())
```

Node.js (`fetch`):

```javascript
const BASE = "https://api.meetstream.ai/api/v1";
const res = await fetch(`${BASE}/bots`, {
  headers: { Authorization: `Token ${process.env.MEETSTREAM_API_KEY}` },
});
console.log(await res.json());
```

For POST requests, also send `Content-Type: application/json` and a JSON body.

## Idempotency & deduplication

Creating a bot is a costly, side-effecting action (a bot literally joins a
call). To make retries safe, pass a **deduplication key** on `create_bot` (also
surfaced as `deduplication_key` in scheduled `bot_config`). If the same key is
submitted twice, MeetStream returns the original bot instead of spawning a
duplicate. Always set one when a network retry could resend the request. See the
docs page `guides/features/deduplication-idempotency-keys.md` for specifics.

## Custom attributes

Most bot-creating calls accept `custom_attributes` — an arbitrary key/value
object that is echoed back on the bot object, in status responses, and in every
webhook payload. Use it to correlate a bot with your own records (e.g.
`{"user_id": "u_123", "call_id": "c_456"}`) instead of maintaining a separate
mapping. You can also filter `GET /bots` by these via `custom_attr[key]=value`.

## Pagination (list endpoints)

`GET /bots` returns a cursor-paginated shape:

```json
{ "bots": [ /* ... */ ], "hasNextPage": false, "nextCursor": "string" }
```

When `hasNextPage` is true, pass `nextCursor` back to fetch the next page.
`GET /bots` also supports filters: `status`, `from` / `to` (YYYY-MM-DD),
`platform` (GMeet | Zoom | Teams), and `custom_attr[...]`.

## Errors

Standard HTTP status codes. On failure, check in this order:

1. **401 / auth errors** → the `Token ` scheme or a bad/missing key.
2. **404** → missing `/api/v1`, or a path that doesn't match a documented one
   (don't invent paths — see the lookup section).
3. **4xx on create_bot** → an invalid `meeting_link` or a malformed nested
   config object (e.g. `recording_config`, `live_audio_required`).

## Looking things up instead of guessing

The docs are designed for LLMs. Resolve unknowns rather than inventing them:

- **`.md` suffix:** any docs page + `.md` returns clean markdown, e.g.
  `https://docs.meetstream.ai/api-reference/api-endpoints/bot-endpoints/get-bot-summary.md`.
- **Index:** https://docs.meetstream.ai/llms.txt — every doc URL in one file.
- **OpenAPI:** https://docs.meetstream.ai/openapi.json / `openapi.yaml` — exact
  request/response schemas for every endpoint.
- **MCP server:** https://docs.meetstream.ai/_mcp/server for live doc context in
  MCP-capable clients.

## Migrating from Recall.ai

If the user is porting an existing Recall.ai integration, there's a dedicated
mapping of endpoint and field rewrites at
https://docs.meetstream.ai/migration/migrate-from-recall.md.
