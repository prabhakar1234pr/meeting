# CLAUDE.md

Project context for the "agents that take actions" hackathon.

## Hackathon

**Build agents that take actions** — Saturday, July 25 · San Francisco.

Most agents are demos: they call APIs and look impressive, but fall apart the
moment they need to act **as real users**. This hackathon is about that last
mile — an AI agent that doesn't just execute tasks, but does it **as the right
person, with the right access, in the right context**.

The hard part isn't connecting an agent to a CRM (or any app) — it's updating a
record **as a specific user**, respecting their tenant, their permissions, their
identity. Act on behalf of real users: **not as root, not as a service account,
but as the person the action belongs to.**

### Track — real-time call agent

Build an AI agent that **joins a live call and provides active assistance during
the conversation itself.**

Multi-modal and real-time: the agent can **talk, send messages, and pull live
context from external tools** — all mid-conversation. For example: surface a
caller's account status, flag open tickets, or alert the rep to a churn risk
while the call is happening.

**Scalekit governs exactly what context the agent can access and for which
user**, so nothing bleeds across accounts.

## Our stack

Two APIs, each covering half of the problem:

- **MeetStream** — puts the agent *into the live call*: joins Zoom/Meet/Teams,
  streams live audio, runs a voice agent (MIA) that can talk and post chat
  messages in real time.
- **Scalekit AgentKit** — governs *identity and access*: the agent pulls live
  context from external tools (Gmail, CRM, tickets…) **as the specific user**,
  via per-user connected accounts and scoped OAuth tokens — never as a shared
  service account.

The demo flow: agent joins a call (MeetStream) → hears the conversation → pulls
that caller's data from external tools (Scalekit, scoped to that user) → speaks
or messages the insight back into the call.

## Repo layout

- `gmail_agent.py` — the original Scalekit AgentKit example: creates a per-user
  connected account, runs OAuth, fetches fresh scoped tokens, and calls the
  Gmail API as that user. The template for "act as the right person."
- `app/` — the full app (FastAPI + web UI). The `gmail_agent.py` pattern is now
  generalized into `app/scalekit_client.py` (per-user connected accounts + fresh
  scoped tokens) and `app/tools.py` (the action registry: `github_create_issue`,
  `gmail_send_email`). Chat is **tool-aware**: it proposes an action, waits for a
  human confirm, runs it **as the logged-in user**, and logs it to an audit
  trail (Activity). `app/auth.py` adds Scalekit **SaaSKit** login + orgs + roles.
  See `README.md` for the full map.
- `.env.example` — required Scalekit credentials (`SCALEKIT_ENVIRONMENT_URL`,
  `SCALEKIT_CLIENT_ID`, `SCALEKIT_CLIENT_SECRET`). Copy to `.env`; never commit
  real keys.
- `requirements.txt` — `scalekit-sdk-python`, `python-dotenv`, `requests`.
- `.claude/skills/meetstream/` — local skill: the full MeetStream API reference
  (bots, live audio, webhooks, transcription, MIA voice agents, calendar).
- `.claude/settings.json` — enables the Scalekit `authstack` plugins
  (`agentkit@authstack`, `saaskit@authstack`).

## Working notes

- **Setup:** `pip install -r requirements.txt`, then `cp .env.example .env` and
  fill in Scalekit credentials from app.scalekit.com → Developers → Settings →
  API Credentials.
- **Scalekit is the identity layer (now wired in)** — actions run through a
  per-user Scalekit connected account: `app/scalekit_client.py` fetches a fresh
  scoped token for the logged-in user right before each call, and `app/tools.py`
  executes as them. Every action is gated behind a human confirm and recorded in
  the Activity audit trail. Never shortcut this with a global/service token —
  it's the point of the hackathon. `AUTH_ENABLED=false` runs open as a single
  `local` user for dev.
- **Connectors:** Gmail works with no dashboard setup. **Every other connector
  (GitHub, Slack, CRM…) must be created once in the Scalekit Dashboard**
  (AgentKit → Connections) with a Connection Name that matches the app's config
  (`SCALEKIT_CONNECTION_GITHUB`, default `github`). A missing connection returns
  `RESOURCE_NOT_FOUND: connection not found`.
- **MeetStream is the real-time layer** — for the live-call agent, use MIA
  (voice agent) + live audio streaming. The local `meetstream` skill has the
  exact endpoints, auth (`Authorization: Token ...`), and payload shapes.
- **Docs:** Scalekit https://docs.scalekit.com/llms.txt · MeetStream
  https://docs.meetstream.ai/llms.txt
