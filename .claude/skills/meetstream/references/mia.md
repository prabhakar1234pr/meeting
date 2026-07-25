# MIA — Meeting Intelligence Agents

MIA is MeetStream's voice-agent layer: an AI agent that listens and **speaks** in
the meeting (not just a passive recorder). You define an agent **config** once,
then attach it to any bot by passing its `agent_config_id` to `create_bot`.

Flow: **create an agent config → attach it to a bot via `agent_config_id`**.

## Create an agent config — `POST /mia`

### Required fields

| Field | Type | Description |
|---|---|---|
| `agent_name` | string | Name/identifier for the agent |
| `mode` | string | `"pipeline"` or `"realtime"` (see below) |
| `model` | object | LLM configuration (see below) |

### `mode`: pipeline vs. realtime

- **`pipeline`** — composes separate STT → LLM → TTS components. You configure a
  `transcriber` (STT) and a `voice` (TTS) alongside the `model`. More control and
  provider mixing.
- **`realtime`** — uses a single speech-to-speech realtime model (e.g.
  `gpt-realtime-mini`) with `modalities` like `["text", "audio"]` and a `voice`
  on the model itself. Lower latency, fewer moving parts.

### `model` object

| Field | Required | Notes |
|---|---|---|
| `provider` | yes | e.g. `"openai"`, `"google"`, `"xai"` |
| `model` | yes | e.g. `"gpt-4o-mini"`, `"gpt-realtime-mini"` |
| `system_prompt` | yes | Agent behavior/instructions |
| `first_message` | no | Opening line the agent says |
| `temperature` | no | 0–2 |
| `max_tokens` / `max_response_output_tokens` | no | Response cap |
| `voice` | no | Voice id (for realtime models) |
| `modalities` | no | e.g. `["text", "audio"]` |
| `top_p`, `frequency_penalty`, `presence_penalty` | no | Sampling controls |
| `thinking_config` | no | Extended reasoning settings |

### Optional nested objects

- **`voice`** — TTS config for pipeline mode: `{ provider, model, voice_id, speed }`.
- **`transcriber`** — STT config: `{ provider, model, language, boostwords }`.
- **`agent`** — behavior: `tools` (array), interruption/VAD/endpointing settings,
  `preemptive_generation`, `user_away_timeout`.
- **`audio`** — `{ sample_rate, num_channels }`.
- **`wake_word`** — `{ enabled, words: [...], timeout }` or `null`.
- **`Avatar`** — `{ provider, enabled, avatar_id }` for a visual avatar.

### Example — pipeline mode

```json
{
  "agent_name": "Meeting assistant",
  "mode": "pipeline",
  "model": {
    "provider": "openai",
    "model": "gpt-4o-mini",
    "system_prompt": "You are a helpful meeting assistant.",
    "temperature": 0.8,
    "max_tokens": 2048
  },
  "voice": { "provider": "elevenlabs", "model": "tts-1", "voice_id": "alloy" },
  "transcriber": { "provider": "openai", "model": "whisper-1", "language": "en" },
  "agent": { "tools": ["current_time", "weather_now"], "preemptive_generation": true,
             "user_away_timeout": 15 },
  "audio": { "sample_rate": 24000, "num_channels": 1 }
}
```

Response (`201 Created`):

```json
{
  "message": "Agent configuration created successfully.",
  "agent_config_id": "uuid-string",
  "agent_config": { "AgentConfigID": "uuid", "AgentName": "...", "Mode": "pipeline", ... }
}
```

Save `agent_config_id`.

## Attach the agent to a bot

Pass the id when creating the bot:

```json
{
  "meeting_link": "https://meet.google.com/abc-defg-hij",
  "bot_name": "Assistant",
  "agent_config_id": "the-uuid-from-above"
}
```

The bot will now run the agent — speaking in the meeting via MeetStream's
two-way audio bridge (see `socket_connection_url` in `websockets.md`).

## Managing configs

Beyond create, MIA has update / get / delete config endpoints. Resolve their
exact paths via the docs lookup in `api-conventions.md`, and see
`guides/mia/mia-configurations.md` for the full option catalog across
pipeline/realtime, OpenAI/Gemini/Grok, avatars, and advanced VAD/endpointing.
