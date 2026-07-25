# AI Team Member — Scope (v1)

An AI teammate for software teams. It joins meetings and **speaks** live, remembers
every meeting, is grounded in a shared **Knowledge Store** (GitHub repos and
uploaded files), and can be chatted with afterward. Local-first.

## v1 "done" means you can

1. **Knowledge Store** — a dedicated page to add context sources (a GitHub repo,
   or an uploaded file: PDF / DOCX / Markdown / text). Adding a source **triggers
   the ingestion pipeline** (fetch/parse → chunk → embed → local vector store)
   and shows its status.
2. **Create an agent** — name, **own system prompt**, wake phrase, voice — then
   **connect existing sources** from the Knowledge Store to it (many-to-many).
3. **Call it into a meeting** — paste a meeting link; a MeetStream bot joins with
   live transcription + a two-way audio bridge.
4. **It answers live, out loud** — when addressed by its wake phrase, it
   retrieves from its connected sources + past meetings and **speaks** back in
   **under 2 seconds**.
5. **It writes up the meeting** — on meeting end: **brief + notes + transcript**,
   saved to **Past Meeting Briefs** and folded into the agent's memory.
6. **Chat with it** — a conversation that remembers **every past meeting and
   every connected source**.

## Non-goals for v1 (deferred, but designed-for)

- Extra bus consumers: fact-checker, Slack messaging, calendar updates. The
  Redis transcription bus makes each of these a new consumer — no core changes.
- **Per-user/tenant access via Scalekit.** Every row carries a `tenant_id` now,
  so later the agent ingests a *private* repo and reads memory **as the right
  user**, with nothing bleeding across accounts (the hackathon thesis).
- Calendar auto-join, multi-tenant admin, auth/login, polish.

## Architecture (all local; LLM via OpenAI, voice via ElevenLabs)

```
 Meeting (Zoom/Meet/Teams)
   │  MeetStream bot: live_transcription webhook + socket_connection_url (audio bridge)
   ▼
 FastAPI ──(transcription webhook)──► REDIS STREAM  transcript:{bot_id}   ← the bus
   │                                        │
   │  (holds the /bridge control socket)    ├──────────────┬─────────────────────┐
   ▼                                        ▼              ▼                     ▼
 responder (co-located w/ socket)     notetaker      [future: factcheck,   [future: slack,
  wake-word → RAG → OpenAI (mini)      on bot.done:   each a new consumer    calendar, …]
  → ElevenLabs → PCM16 → sendaudio      brief+notes    on the same stream]
   │        ▲                             + embed
   │        │ retrieve
   ▼        │
 Chroma (vector): connected SOURCES + agent MEMORY      ◄── ingestion pipeline
 SQLite: agents, sources, agent_sources, meetings, briefs    (github/docs/website)
   ▲
 Minimal Web UI  (Knowledge Store · Agents · Meetings · Past Briefs · Chat)
 + Chat API (RAG over connected sources + all past meetings)
```

The Redis stream (with consumer groups) is the backbone: it decouples "hearing"
the meeting from every service that reacts to it, so new services just subscribe.

## Data model (SQLite)

- **sources** — `id, name, type(github|file), uri, status(pending|
  ingesting|ready|error), chunk_count, error, tenant_id, created_at`. The
  Knowledge Store. Standalone and reusable. (`uri` is a repo URL, or the local
  path of an uploaded file.)
- **agents** — `id, name, system_prompt, wake_phrase, voice, tenant_id,
  created_at`.
- **agent_sources** — `agent_id, source_id` (many-to-many link).
- **meetings** — `id, agent_id, bot_id, transcript_id, meeting_link, title,
  status, started_at, ended_at`.
- **briefs** — `id, meeting_id, brief, notes, transcript_path, created_at`.

Vectors live in **Chroma** with metadata `{kind: "source"|"meeting", source_id,
agent_id, meeting_id}`. Answer-time retrieval filters to the agent's connected
`source_id`s (knowledge) plus its `agent_id` meetings (memory).

## Tech stack

- **Python 3.11 · FastAPI + uvicorn** — webhooks, REST, `/bridge` WebSocket,
  serves the web UI.
- **Redis Streams** (docker-compose) — the transcription bus.
- **SQLite** — metadata.
- **Chroma + fastembed** — local vector memory + local embeddings (onnx, no
  torch), so memory is fully offline.
- **OpenAI** (`gpt-4o-mini` responder for low latency, `gpt-4o` for briefs;
  both configurable) via the `openai` SDK, behind a swappable `llm.py`.
- **ElevenLabs Text-to-Speech** (`eleven_flash_v2_5`, streaming, raw PCM output)
  — the voice; the bridge resamples to PCM16 48 kHz mono for MeetStream's
  `sendaudio`.
- **MeetStream** — `create_bot` (live transcription + `socket_connection_url` +
  `callback_url`); `sendaudio` / `interrupt` / `sendchat` to talk in the call.
  See the local `meetstream` skill for exact shapes.

## Live-answer latency budget (<2s)

transcript segment → Redis (~ms) → wake/end-of-turn detect (~ms) → local RAG
(~50–150ms) → OpenAI (gpt-4o-mini) **streaming** first tokens (~300–600ms) →
streaming ElevenLabs Flash TTS → `sendaudio` chunks. Stream LLM→TTS→audio so
speech starts before the full answer is generated; `interrupt` handles barge-in.

## Credentials / local run

- `MEETSTREAM_API_KEY`
- `OPENAI_API_KEY` — the LLM.
- `ELEVENLABS_API_KEY` — voice (TTS).
- **Tunnel:** MeetStream must reach our webhooks + bridge from the internet, so
  run a tunnel (ngrok/cloudflared) and set `PUBLIC_BASE_URL` / `PUBLIC_WS_URL`.

## Build phases

1. Infra skeleton — Redis (compose), FastAPI app, SQLite schema, config,
   MeetStream client.
2. Transcription bus — transcription webhook → Redis stream; prove it flows.
3. Knowledge Store + agents — add source (→ ingestion → Chroma), create agent,
   connect sources.
4. Live voice responder — wake → RAG → OpenAI → ElevenLabs → `sendaudio`,
   measured < 2s.
5. Post-meeting — brief/notes/transcript on `bot.done`, stored + embedded.
6. Web UI + chat — the five pages, and chat over connected sources + all
   meetings.

(`gmail_agent.py` from the earlier Scalekit demo is out of scope and left as-is.)
