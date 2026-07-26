"""Central configuration, loaded from environment (.env)."""
import os

from dotenv import load_dotenv

# override=True so .env is authoritative: a stale value left in the shell
# environment (e.g. an old OPENAI_API_KEY) can't silently shadow the file.
load_dotenv(override=True)


def _get(key: str, default=None):
    val = os.getenv(key)
    return val if val not in (None, "") else default


# ─── MeetStream ───────────────────────────────────────────────
MEETSTREAM_API_KEY = _get("MEETSTREAM_API_KEY")
MEETSTREAM_BASE_URL = "https://api.meetstream.ai/api/v1"
PUBLIC_BASE_URL = _get("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")
PUBLIC_WS_URL = _get("PUBLIC_WS_URL", "ws://localhost:8000").rstrip("/")
TRANSCRIPT_PROVIDER = _get("TRANSCRIPT_PROVIDER", "deepgram_streaming")

# ─── LLM (OpenAI-compatible) ──────────────────────────────────
# The whole app talks to one OpenAI-compatible endpoint via app/llm.py. Leave
# LLM_BASE_URL unset for OpenAI; point it at a compatible endpoint to swap
# providers with no code change. For Google Gemini:
#   LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/
OPENAI_API_KEY = _get("OPENAI_API_KEY")
LLM_BASE_URL = _get("LLM_BASE_URL")
# The API key for the LLM endpoint. Default (no base URL) = OpenAI, so use the
# OpenAI key. Only when LLM_BASE_URL points at another provider do we fall back
# to provider key names (a Google AI Studio key is often stored as GEMINI/VERTEX)
# — otherwise a stray VERTEX_API_KEY in .env would hijack OpenAI calls.
if LLM_BASE_URL:
    LLM_API_KEY = (_get("LLM_API_KEY") or _get("GEMINI_API_KEY")
                   or _get("VERTEX_API_KEY") or _get("GOOGLE_API_KEY") or OPENAI_API_KEY)
else:
    LLM_API_KEY = _get("LLM_API_KEY") or OPENAI_API_KEY
_is_gemini = bool(LLM_BASE_URL and "generativelanguage" in LLM_BASE_URL)
# Model defaults follow the provider: Gemini flash is fast enough for the live
# loop and capable enough for briefs. Override either via env.
LLM_RESPONDER_MODEL = _get("LLM_RESPONDER_MODEL",
                           "gemini-2.5-flash" if _is_gemini else "gpt-4o-mini")
LLM_BRIEF_MODEL = _get("LLM_BRIEF_MODEL",
                       "gemini-2.5-flash" if _is_gemini else "gpt-4o")

# ─── ElevenLabs — Text-to-Speech (voice) ──────────────────────
ELEVENLABS_API_KEY = _get("ELEVENLABS_API_KEY")
ELEVENLABS_VOICE_ID = _get("ELEVENLABS_VOICE_ID", "JBFqnCBsd6RMkjVDRZzb")
ELEVENLABS_MODEL = _get("ELEVENLABS_MODEL", "eleven_flash_v2_5")
# ElevenLabs emits raw PCM16 at this rate; the bridge resamples to 48k for
# MeetStream's sendaudio. pcm_24000 is broadly available across plans.
ELEVENLABS_OUTPUT_FORMAT = _get("ELEVENLABS_OUTPUT_FORMAT", "pcm_24000")
ELEVENLABS_OUTPUT_RATE = int(_get("ELEVENLABS_OUTPUT_RATE", "24000"))
MEETSTREAM_AUDIO_RATE = 48000  # MeetStream sendaudio expects 48kHz PCM16 mono

# ─── Redis ────────────────────────────────────────────────────
REDIS_URL = _get("REDIS_URL", "redis://localhost:6379/0")

# ─── App ──────────────────────────────────────────────────────
APP_HOST = _get("APP_HOST", "0.0.0.0")
APP_PORT = int(_get("APP_PORT", "8000"))
DB_PATH = _get("DB_PATH", "./data/app.db")
CHROMA_PATH = _get("CHROMA_PATH", "./data/chroma")
DEFAULT_TENANT_ID = _get("DEFAULT_TENANT_ID", "local")

# ─── Scalekit AgentKit — identity / scoped-token layer for actions ──
# Actions run "as" a user via a Scalekit connected account. For now that's a
# single operator (the agent's owner); per-caller identities come later.
SCALEKIT_ENVIRONMENT_URL = _get("SCALEKIT_ENVIRONMENT_URL")
SCALEKIT_CLIENT_ID = _get("SCALEKIT_CLIENT_ID")
SCALEKIT_CLIENT_SECRET = _get("SCALEKIT_CLIENT_SECRET")
OPERATOR_USER_ID = _get("OPERATOR_USER_ID", "operator")
# Exact Scalekit Dashboard "Connection Name" per connector. Gmail works with no
# dashboard setup; GitHub must be created once (AgentKit → Connections).
SCALEKIT_CONNECTION_GMAIL = _get("SCALEKIT_CONNECTION_GMAIL", "gmail")
SCALEKIT_CONNECTION_GITHUB = _get("SCALEKIT_CONNECTION_GITHUB", "github")
# Default org/repo for GitHub issues when the model doesn't specify one.
GITHUB_DEFAULT_REPO = _get("GITHUB_DEFAULT_REPO")  # e.g. "my-org/my-repo"

# Standard wake word that addresses ANY agent in a meeting. An agent's own name
# and wake phrase also work as additional triggers.
WAKE_WORD = _get("WAKE_WORD", "hey assistant")

# ─── Auth / sessions (Scalekit Full Stack Auth) ───────────────
# The BROWSER logs in via localhost (not the ngrok tunnel, which is only for
# MeetStream's server-to-server webhooks). This redirect URI must be registered
# in the Scalekit dashboard.
AUTH_REDIRECT_URI = _get("AUTH_REDIRECT_URI", f"http://localhost:{APP_PORT}/auth/callback")
# Where Scalekit sends the browser AFTER logout. This exact value must be
# registered in the Scalekit dashboard (Authentication → Redirect URLs) — same
# allowlist as the login callback, and matched exactly (no trailing slash, per
# Scalekit's own examples). Set AUTH_POST_LOGOUT_URI= (empty) to omit it, in
# which case Scalekit just shows its own signed-out page instead of returning.
AUTH_POST_LOGOUT_URI = _get("AUTH_POST_LOGOUT_URI", f"http://localhost:{APP_PORT}")
AUTH_SCOPES = ["openid", "profile", "email", "offline_access"]
SESSION_COOKIE = "teammate_sid"
SESSION_TTL_SECONDS = int(_get("SESSION_TTL_SECONDS", str(30 * 24 * 3600)))
# When true, /api/* require login + org (Scalekit FSA). Default OFF so the app
# keeps working in open/dev mode until Scalekit FSA is configured; flip to true
# (AUTH_ENABLED=true in .env) once login + org creation are set up.
AUTH_ENABLED = _get("AUTH_ENABLED", "false").lower() in ("1", "true", "yes")


# ─── Derived / helpers ────────────────────────────────────────
def transcript_stream(bot_id: str) -> str:
    """Redis Stream key carrying one meeting's live transcript."""
    return f"transcript:{bot_id}"


# Public webhook / bridge URLs handed to MeetStream on create_bot.
TRANSCRIPTION_WEBHOOK_URL = f"{PUBLIC_BASE_URL}/webhooks/transcription"
LIFECYCLE_WEBHOOK_URL = f"{PUBLIC_BASE_URL}/webhooks/lifecycle"
CONTROL_WS_URL = f"{PUBLIC_WS_URL}/bridge"  # socket_connection_url (audio out)
