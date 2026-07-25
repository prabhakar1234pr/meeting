# AI Team Member

An AI teammate for software teams: it joins meetings and **speaks** live, is
grounded in a shared **Knowledge Store** (GitHub repos and uploaded files),
remembers every meeting, can be chatted with afterward, and **takes actions as
the right user** — filing a GitHub issue or sending an email through that
person's own Scalekit-scoped token, gated behind a human confirm, never a shared
service account. Local-first.

See [SCOPE.md](SCOPE.md) for the full plan and architecture.

## Stack

- **FastAPI** — auth, webhooks, REST, the audio-bridge WebSocket, and the web UI
- **Redis Streams** — the transcription bus (`transcript:{bot_id}`)
- **SQLite** — agents, sources, meetings, briefs, connected accounts, actions,
  users/orgs/sessions
- **Chroma + fastembed** — local vector memory + embeddings (offline)
- **OpenAI** — the brain, incl. tool-calling (via `OPENAI_API_KEY`)
- **ElevenLabs** (`eleven_flash_v2_5`) — the voice
- **MeetStream** — puts the agent into Zoom / Meet / Teams
- **Scalekit** — identity & access: **AgentKit** for per-user connected accounts
  + scoped tokens (acting as the right user) and **SaaSKit** for login, orgs,
  and admin/member roles

## Setup

```bash
# 1. Python deps
python -m venv .venv && source .venv/Scripts/activate   # Windows Git Bash
pip install -r requirements.txt

# 2. Config
cp .env.example .env         # then fill in the keys (MeetStream, OpenAI, ElevenLabs)

# 3. Redis (the transcription bus)
docker compose up -d redis

# 4. A public tunnel so MeetStream can reach your webhooks + audio bridge
ngrok http 8000              # copy the https URL into PUBLIC_BASE_URL / PUBLIC_WS_URL

# 5. Run the app
uvicorn app.api.main:app --reload --port 8000
```

## What's built

- **Web UI** at `http://localhost:8000` — Knowledge, Agents, Create agent,
  Meetings, Briefs, Chat, Connections, Activity, Organization.
- **Knowledge Store** — add a GitHub repo, or upload a file (PDF / DOCX /
  Markdown / text); ingestion runs automatically (fetch/parse → chunk → embed →
  Chroma) and the status flips to `ready`.
- **Agents** — create an agent with its own system prompt + wake phrase, and
  connect Knowledge Store sources to it (many-to-many) from its config page.
- **Meetings** — send an agent into a meeting link; a MeetStream bot joins with
  live transcription + the two-way audio bridge.
- **Live voice responder** — on hearing its wake phrase, the agent does RAG over
  its connected sources + past meetings, streams an OpenAI answer, and **speaks**
  it back sentence-by-sentence (targeting <2s to first audio).
- **Post-meeting** — on `bot.done`, brief + notes + transcript are generated and
  saved to Briefs, and folded into the agent's memory.
- **Chat** — talk to an agent; it retrieves over every past meeting + connected
  source, and is **tool-aware** (see Actions).
- **Actions — take action as the right user.** On **Connections**, each user
  links their own Gmail / GitHub (Scalekit AgentKit). In Chat, ask the agent to
  do something (file an issue, send an email); it **proposes** the action, you
  **confirm**, and it runs with *your own* scoped token. Every action is logged
  on **Activity** with the identity it ran as. GitHub issue repos come from the
  agent's connected GitHub sources (no hardcoded default repo).
- **Auth & orgs** — Scalekit SaaSKit login, organizations, and admin/member
  roles. Toggle with `AUTH_ENABLED`; `false` runs open as a single `local` user.

Quick check once running: `curl localhost:8000/health`.

### Testing the live path
The voice loop needs a real meeting: create a source (wait for `ready`), create
an agent and connect the source, then on the Meetings tab send it into a live
Zoom/Meet/Teams link. Speak the wake phrase and it should answer out loud. The
brief appears under Briefs after the meeting ends.

### Actions (Scalekit AgentKit)
Set `SCALEKIT_ENVIRONMENT_URL` / `SCALEKIT_CLIENT_ID` / `SCALEKIT_CLIENT_SECRET`
in `.env`. On the **Connections** page, connect an account:

- **Gmail** works with no dashboard setup — click Connect and authorize.
- **GitHub (and any other connector) must be created once** in the Scalekit
  Dashboard (AgentKit → Connections → + Create Connection), with a Connection
  Name matching `SCALEKIT_CONNECTION_GITHUB` (default `github`) and OAuth scopes
  that include `repo`. A missing connection returns
  `RESOURCE_NOT_FOUND: connection not found`.

Then in Chat: *"open an issue titled '…' in our backend repo."* Confirm the
proposed action; it runs as you and appears on **Activity**. `AUTH_ENABLED=false`
runs as the `local` user so you can test without logging in.

## Later (designed-for, not yet built)

- **Per-caller identity in live calls** — map a meeting speaker to their own
  Scalekit connected account so the agent acts as *that caller* mid-call (today,
  actions run as the logged-in user).
- **Actions in the voice loop** — wire the same tool-calling into the in-meeting
  responder so "Hey Ada, file an issue" works by voice.
- More connectors (Slack, CRM, tickets) and extra bus consumers (fact-checker,
  calendar updates) on the same `transcript:{bot_id}` stream.

## Layout

```
app/
  config.py           env/settings
  db.py               SQLite: sources, agents, agent_sources, meetings, briefs,
                      connected_accounts, actions, users, orgs, memberships, sessions
  auth.py             Scalekit SaaSKit: login, orgs, memberships, sessions, RBAC
  scalekit_client.py  Scalekit AgentKit: per-user connected accounts + scoped tokens
  tools.py            action registry (github_create_issue, gmail_send_email)
  bus.py              Redis Streams helpers (the transcription bus)
  meetstream.py       MeetStream client + control-socket command builders
  api/main.py         FastAPI: auth, webhooks, REST, /bridge socket, serves web UI
  ingest/             source fetch → chunk → embed
  services/           responder, notetaker
  web/                the web UI
```
