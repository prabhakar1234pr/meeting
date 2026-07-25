"""FastAPI app: MeetStream webhooks, REST API, and the audio-bridge socket.

Run:  uvicorn app.api.main:app --reload --port 8000
(Redis must be up: `docker compose up -d redis`.)
"""
import asyncio
import contextlib
import json
import logging
import os
import sys
import time

from fastapi import (Depends, FastAPI, File, Form, Request, UploadFile, WebSocket,
                     WebSocketDisconnect)
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               RedirectResponse, StreamingResponse)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import auth, bus, config, db, llm, meetstream, scalekit_client, tools, vectorstore
from ..ingest.run import ingest_source
from ..services import notetaker
from ..services.responder import Responder

# One handler for the whole "teammate.*" tree so our logs show in the uvicorn
# console at INFO (uvicorn doesn't configure arbitrary loggers by default).
_root = logging.getLogger("teammate")
if not _root.handlers:
    _h = logging.StreamHandler(sys.stdout)
    _h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    _root.addHandler(_h)
    _root.setLevel(logging.INFO)
    _root.propagate = False

log = logging.getLogger("teammate.api")

# Live control sockets, keyed by bot_id (MeetStream opens socket_connection_url
# to us; the responder speaks back over the stored socket).
control_sockets: dict[str, WebSocket] = {}

# Pending debounced finalizations, keyed by bot_id. When the audio bridge drops
# we wait a short grace period before ending the meeting, so a transient
# reconnect doesn't prematurely brief a still-live call; a fresh handshake for
# the same bot cancels the pending finalize.
_pending_finalize: dict[str, "asyncio.Task"] = {}
_FINALIZE_GRACE_SECONDS = 30


async def _finalize_meeting(meeting: dict) -> None:
    """End a meeting and build its brief — idempotent. `begin_briefing` is an
    atomic claim, so whichever signal fires first (lifecycle webhook, bridge
    disconnect, or startup recovery) wins and the rest are no-ops. This is why a
    dropped lifecycle webhook no longer leaves a meeting stuck 'in_meeting' with
    no brief."""
    if not db.begin_briefing(meeting["id"]):
        return
    fresh = db.get_meeting(meeting["id"]) or meeting
    await notetaker.process(fresh)


async def _delayed_finalize(bot_id: str) -> None:
    try:
        await asyncio.sleep(_FINALIZE_GRACE_SECONDS)
    except asyncio.CancelledError:
        return
    _pending_finalize.pop(bot_id, None)
    meeting = db.get_meeting_by_bot(bot_id)
    if meeting:
        await _finalize_meeting(meeting)


async def _recover_stuck_meetings() -> None:
    """Finalize meetings left mid-flight by a crash or a missed lifecycle webhook.
    Runs once at startup — any meeting still 'in_meeting'/'briefing' now has no
    live bot, so we brief it from whatever the transcript bus still holds (or mark
    it ended if nothing was captured)."""
    for m in db.meetings_in_progress():
        log.info("recovering stuck meeting=%s status=%s", m["id"], m.get("status"))
        try:
            await _finalize_meeting(m)
        except Exception:  # noqa: BLE001
            log.exception("recovery failed for meeting=%s", m["id"])


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    asyncio.create_task(_recover_stuck_meetings())
    yield
    await bus.close()


app = FastAPI(title="AI Team Member", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"ok": True, "ts": int(time.time())}


# ─── Auth (Scalekit FSA) + organizations ──────────────────────
@app.get("/auth/login")
async def auth_login(switch: int = 0, idp_initiated_login: str | None = None,
                     error: str | None = None, error_description: str | None = None):
    # This is also the Initiate Login URI registered in the Scalekit dashboard,
    # so it must handle flows that start OUTSIDE the app — chiefly clicking
    # "Accept invite" in an invitation email. Scalekit sends those here with an
    # `idp_initiated_login` token; we decode it and resume the OAuth flow for the
    # right org/user so they land in the app instead of Scalekit's hosted page.
    if error:
        return RedirectResponse(f"/app?auth_error={error_description or error}")
    if idp_initiated_login:
        try:
            c = auth.idp_initiated_claims(idp_initiated_login)
            url = auth.authorization_url(
                organization_id=c.get("organization_id"),
                connection_id=c.get("connection_id"),
                login_hint=c.get("login_hint"),
                state=c.get("relay_state"),
            )
            return RedirectResponse(url)
        except Exception as e:  # noqa: BLE001
            log.exception("idp-initiated login failed")
            return RedirectResponse(f"/app?auth_error={e}")
    # `?switch=1` forces the account picker / login form so you can sign in as a
    # different user instead of being silently re-logged into the SSO session.
    prompt = "login" if switch else None
    return RedirectResponse(auth.authorization_url(prompt=prompt))


@app.get("/auth/callback")
async def auth_callback(code: str | None = None, error: str | None = None):
    if error or not code:
        return RedirectResponse(f"/app?auth_error={error or 'missing_code'}")
    try:
        result = auth.exchange_code(code)
        sid = auth.establish_session(result)
    except Exception as e:  # noqa: BLE001
        log.exception("auth callback failed")
        return RedirectResponse(f"/app?auth_error={e}")
    resp = RedirectResponse("/app")
    resp.set_cookie(config.SESSION_COOKIE, sid, httponly=True, samesite="lax",
                    max_age=config.SESSION_TTL_SECONDS, path="/")
    return resp


@app.post("/auth/logout")
async def auth_logout(request: Request):
    sid = request.cookies.get(config.SESSION_COOKIE)
    logout = None
    if sid:
        s = db.get_session(sid)
        db.delete_session(sid)
        try:
            if config.AUTH_ENABLED and s and s.get("id_token"):
                logout = auth.logout_url(s["id_token"])
        except Exception:  # noqa: BLE001
            pass
    resp = JSONResponse({"ok": True, "logout_url": logout})
    resp.delete_cookie(config.SESSION_COOKIE, path="/")
    return resp


@app.get("/api/me")
async def api_me(user: dict = Depends(auth.current_user)):
    org = db.get_org(user["org_id"]) if user.get("org_id") else None
    return {
        "user": {"id": user["user_id"], "email": user["email"], "name": user["name"]},
        "org": ({"id": org["id"], "name": org["name"]} if org
                else ({"id": user["org_id"], "name": "Local"} if user.get("org_id") else None)),
        "role": user.get("role"),
        "auth_enabled": config.AUTH_ENABLED,
    }


class OrgIn(BaseModel):
    name: str


@app.post("/api/orgs")
async def create_org_endpoint(body: OrgIn, user: dict = Depends(auth.current_user)):
    """Create an org (in Scalekit for invitations + locally); caller becomes admin."""
    scalekit_org_id = None
    if config.AUTH_ENABLED:
        try:
            from scalekit.v1.organizations.organizations_pb2 import CreateOrganization
            resp = auth.unwrap(auth.get_client().organization.create_organization(
                CreateOrganization(display_name=body.name)))
            scalekit_org_id = resp.organization.id
        except Exception as e:  # noqa: BLE001
            log.exception("scalekit create_organization failed")
            return JSONResponse({"error": f"could not create org: {e}"}, status_code=502)
    org = db.create_org(body.name, scalekit_org_id, user["user_id"])
    db.add_membership(user["user_id"], org["id"], role="admin", status="active")
    if user.get("sid"):
        db.update_session(user["sid"], active_org_id=org["id"])
    return {"org": org, "role": "admin"}


@app.get("/api/orgs/members")
async def org_members(user: dict = Depends(auth.require_org)):
    return db.list_org_members(user["org_id"])


class InviteIn(BaseModel):
    email: str
    name: str | None = None


@app.post("/api/orgs/invite")
async def invite_member(body: InviteIn, user: dict = Depends(auth.require_admin)):
    org = db.get_org(user["org_id"])
    if not org or not org.get("scalekit_org_id"):
        return JSONResponse({"error": "org not linked to Scalekit (enable AUTH first)"}, status_code=400)
    try:
        from scalekit.v1.users.users_pb2 import CreateUser, CreateUserProfile
        profile = CreateUserProfile()
        if body.name:
            profile.name = body.name
        resp = auth.unwrap(auth.get_client().users.create_user_and_membership(
            org["scalekit_org_id"], CreateUser(email=body.email, user_profile=profile),
            send_invitation_email=True))
        skid = getattr(getattr(resp, "user", None), "id", None)
    except Exception as e:  # noqa: BLE001
        log.exception("invite failed")
        return JSONResponse({"error": f"invite failed: {e}"}, status_code=502)
    if skid:  # local pending membership → activated when they first log in
        invited = db.upsert_user(skid, body.email, body.name)
        db.add_membership(invited["id"], org["id"], role="member", status="pending")
    return {"ok": True, "email": body.email}


# ─── MeetStream webhooks ──────────────────────────────────────
@app.post("/webhooks/transcription")
async def transcription_webhook(req: Request):
    """Live streaming transcription → Redis bus. Keep this fast (2xx quickly)."""
    body = await req.json()
    bot_id = body.get("bot_id")
    if not bot_id:
        return JSONResponse({"ok": False, "error": "no bot_id"}, status_code=200)
    text = body.get("transcript", "")
    eot = bool(body.get("end_of_turn", False))
    log.info("webhook transcript bot=%s eot=%s speaker=%s text=%r",
             bot_id, eot, body.get("speakerName"), text[:80])
    await bus.publish_transcript(bot_id, {
        "speaker": body.get("speakerName") or body.get("speaker"),
        "text": text,
        "new_text": body.get("new_text", ""),
        "end_of_turn": eot,
        "ts": body.get("timestamp"),
    })
    return {"ok": True}


@app.post("/webhooks/lifecycle")
async def lifecycle_webhook(req: Request):
    """Bot lifecycle events. On bot.done, build the meeting brief."""
    body = await req.json()
    bot_id = body.get("bot_id")
    event = body.get("bot_event", "")
    log.info("lifecycle event=%s bot=%s", event, bot_id)
    meeting = db.get_meeting_by_bot(bot_id) if bot_id else None
    if meeting:
        if event == "bot.inmeeting":
            db.update_meeting(meeting["id"], status="in_meeting")
        elif event in ("bot.stopped", "bot.kicked", "bot.done"):
            # A terminal event is authoritative — finalize now (idempotent).
            # Cancel any pending bridge-disconnect grace timer for this bot.
            pending = _pending_finalize.pop(bot_id, None)
            if pending:
                pending.cancel()
            asyncio.create_task(_finalize_meeting(meeting))
    return {"ok": True}


# ─── Audio bridge (socket_connection_url) ─────────────────────
@app.websocket("/bridge")
async def bridge(ws: WebSocket):
    """MeetStream connects here and sends a `ready` handshake with its bot_id.
    We register the socket and start the live voice responder for the meeting.
    """
    await ws.accept()
    log.info("bridge socket connected")
    bot_id = None
    responder_task = None
    try:
        handshake = await ws.receive_json()
        bot_id = handshake.get("bot_id")
        log.info("bridge handshake: %s", handshake)
        if bot_id:
            control_sockets[bot_id] = ws
            # A reconnect for this bot cancels a pending end-of-meeting timer.
            pending = _pending_finalize.pop(bot_id, None)
            if pending:
                pending.cancel()
            meeting = db.get_meeting_by_bot(bot_id)
            agent = db.get_agent(meeting["agent_id"]) if meeting else None
            if agent:
                responder_task = asyncio.create_task(Responder(bot_id, ws, agent).run())
                log.info("responder task started for bot=%s", bot_id)
            else:
                log.warning("bridge: no meeting/agent for bot=%s — agent will be silent", bot_id)
        else:
            log.warning("bridge: handshake had no bot_id — agent will be silent")
        while True:
            await ws.receive_text()  # drain usermsg/interrupt frames until closed
    except WebSocketDisconnect:
        log.info("bridge socket disconnected bot=%s", bot_id)
    except Exception:
        log.exception("bridge socket error bot=%s", bot_id)
    finally:
        if responder_task:
            responder_task.cancel()
        if bot_id and control_sockets.get(bot_id) is ws:
            control_sockets.pop(bot_id, None)
        # The bridge dropping is our most reliable "meeting ended" signal (the
        # lifecycle webhook is often missed). Finalize after a short grace so a
        # transient reconnect doesn't brief a still-live call.
        if bot_id and bot_id not in _pending_finalize:
            _pending_finalize[bot_id] = asyncio.create_task(_delayed_finalize(bot_id))


# ─── REST: Knowledge Store (sources) ──────────────────────────
class SourceIn(BaseModel):
    name: str
    type: str  # github (file uploads use /api/sources/upload)
    uri: str


@app.post("/api/sources")
async def add_source(body: SourceIn, user: dict = Depends(auth.require_admin)):
    """Add a GitHub repo source (URI-based). File uploads use /api/sources/upload."""
    src = db.create_source(body.name, body.type, body.uri, user["org_id"])
    # Kick off ingestion in a worker thread; status flips pending → ready/error.
    asyncio.create_task(asyncio.to_thread(ingest_source, src["id"]))
    return src


_UPLOAD_DIR = os.path.join("data", "uploads")


@app.post("/api/sources/upload")
async def upload_source(name: str = Form(...), file: UploadFile = File(...),
                        user: dict = Depends(auth.require_admin)):
    """Add a file source (PDF / DOCX / Markdown / text). Saves the file locally,
    then ingests it the same way as any other source."""
    os.makedirs(_UPLOAD_DIR, exist_ok=True)
    safe = os.path.basename(file.filename or "upload")
    dest = os.path.join(_UPLOAD_DIR, f"{int(time.time())}_{safe}")
    with open(dest, "wb") as out:
        out.write(await file.read())
    src = db.create_source(name or safe, "file", dest, user["org_id"])
    asyncio.create_task(asyncio.to_thread(ingest_source, src["id"]))
    return src


@app.get("/api/sources")
async def get_sources(user: dict = Depends(auth.require_org)):
    return db.list_sources(user["org_id"])


@app.delete("/api/sources/{source_id}")
async def delete_source_endpoint(source_id: str, user: dict = Depends(auth.require_admin)):
    """Remove a source: its embeddings (Chroma), any uploaded file, and the row
    (agent connections cascade away)."""
    src = db.get_source(source_id)
    if not src:
        return JSONResponse({"error": "not found"}, status_code=404)
    await asyncio.to_thread(vectorstore.delete_source, source_id)
    if src.get("type") == "file" and src.get("uri") and os.path.exists(src["uri"]):
        try:
            os.remove(src["uri"])
        except OSError:
            pass
    db.delete_source(source_id)
    return {"ok": True}


# ─── REST: Agents ─────────────────────────────────────────────
class AgentIn(BaseModel):
    name: str
    system_prompt: str = ""
    wake_phrase: str = ""
    voice: str | None = None


class ConnectSourcesIn(BaseModel):
    source_ids: list[str]


class AgentUpdateIn(BaseModel):
    name: str | None = None
    system_prompt: str | None = None
    wake_phrase: str | None = None
    voice: str | None = None


@app.post("/api/agents")
async def add_agent(body: AgentIn, user: dict = Depends(auth.require_admin)):
    return db.create_agent(
        body.name, body.system_prompt, body.wake_phrase, body.voice,
        user["org_id"],
    )


@app.patch("/api/agents/{agent_id}")
async def update_agent_endpoint(agent_id: str, body: AgentUpdateIn,
                                user: dict = Depends(auth.require_admin)):
    if not db.get_agent(agent_id):
        return JSONResponse({"error": "not found"}, status_code=404)
    return db.update_agent(
        agent_id, body.name, body.system_prompt, body.wake_phrase, body.voice,
    )


@app.get("/api/agents")
async def get_agents(user: dict = Depends(auth.require_org)):
    return db.list_agents(user["org_id"])


@app.get("/api/agents/{agent_id}")
async def get_one_agent(agent_id: str, user: dict = Depends(auth.require_org)):
    agent = db.get_agent(agent_id)
    if not agent:
        return JSONResponse({"error": "not found"}, status_code=404)
    return agent


@app.post("/api/agents/{agent_id}/sources")
async def connect_sources(agent_id: str, body: ConnectSourcesIn,
                          user: dict = Depends(auth.require_admin)):
    if not db.get_agent(agent_id):
        return JSONResponse({"error": "not found"}, status_code=404)
    db.set_agent_sources(agent_id, body.source_ids)
    return db.get_agent(agent_id)


# ─── REST: Meetings (call an agent into a meeting) ────────────
class MeetingIn(BaseModel):
    agent_id: str
    meeting_link: str
    title: str | None = None


@app.post("/api/meetings")
async def call_into_meeting(body: MeetingIn, user: dict = Depends(auth.require_admin)):
    agent = db.get_agent(body.agent_id)
    if not agent:
        return JSONResponse({"error": "agent not found"}, status_code=404)
    try:
        bot = await meetstream.create_bot(
            meeting_link=body.meeting_link,
            bot_name=agent["name"],
            transcription_webhook=config.TRANSCRIPTION_WEBHOOK_URL,
            control_ws_url=config.CONTROL_WS_URL,
            callback_url=config.LIFECYCLE_WEBHOOK_URL,
            custom_attributes={"agent_id": agent["id"]},
        )
    except meetstream.MeetStreamError as e:
        # Surface MeetStream's actual validation message to the UI + logs.
        return JSONResponse(
            {"error": f"MeetStream rejected create_bot ({e.status})", "detail": e.body},
            status_code=400,
        )
    meeting = db.create_meeting(
        agent_id=agent["id"],
        meeting_link=body.meeting_link,
        title=body.title,
        bot_id=bot.get("bot_id"),
        transcript_id=bot.get("transcript_id"),
        org_id=user["org_id"],
    )
    return {"meeting": meeting, "bot": bot}


# ─── REST: Briefs (past meeting briefs) ───────────────────────
@app.get("/api/briefs")
async def get_briefs(user: dict = Depends(auth.require_org)):
    return db.list_briefs(user["org_id"])


@app.get("/api/meetings")
async def list_meetings_endpoint(user: dict = Depends(auth.require_org)):
    """All meetings with metadata (for the Briefs list view)."""
    return db.list_meetings(user["org_id"])


@app.get("/api/meetings/{meeting_id}")
async def meeting_detail(meeting_id: str, user: dict = Depends(auth.require_org)):
    """One meeting's brief, notes, and transcript (for the detail view)."""
    meeting = db.get_meeting(meeting_id)
    if not meeting:
        return JSONResponse({"error": "not found"}, status_code=404)
    agent = db.get_agent(meeting["agent_id"])
    meeting["agent_name"] = agent["name"] if agent else None
    brief = db.get_brief_for_meeting(meeting_id)
    transcript = None
    if brief and brief.get("transcript_path") and os.path.exists(brief["transcript_path"]):
        try:
            with open(brief["transcript_path"], encoding="utf-8") as f:
                transcript = f.read()
        except OSError:
            pass
    return {"meeting": meeting, "brief": brief, "transcript": transcript}


@app.post("/api/meetings/{meeting_id}/brief")
async def generate_brief(meeting_id: str, user: dict = Depends(auth.require_admin)):
    """Manually (re)generate a meeting's brief from the captured transcript."""
    meeting = db.get_meeting(meeting_id)
    if not meeting:
        return JSONResponse({"error": "meeting not found"}, status_code=404)
    await notetaker.process(meeting)  # synchronous so the caller gets the result
    return {"ok": True, "brief": db.get_brief_for_meeting(meeting_id)}


# ─── REST: Chat (remembers every meeting + connected source) ──
class ChatIn(BaseModel):
    agent_id: str
    message: str


@app.post("/api/chat")
async def chat(body: ChatIn, user: dict = Depends(auth.require_org)):
    agent = db.get_agent(body.agent_id)
    if not agent:
        return JSONResponse({"error": "agent not found"}, status_code=404)
    context = await asyncio.to_thread(
        vectorstore.retrieve, body.message, agent.get("source_ids", []), agent["id"], 8
    )
    system = _chat_system(agent, context)

    # Tell the agent who it's talking to, so "email me" / "message myself"
    # resolves to a real address it can put in a tool call.
    if user.get("email"):
        who = user.get("name") or user["email"]
        system += (
            f"\n\nYou are chatting with {who} ({user['email']}). If they ask you to "
            f"email or message 'me' or themselves, use {user['email']} as the recipient."
        )

    # Org roster: lets the agent address teammates by name ("email Ada the notes").
    if user.get("org_id"):
        try:
            roster = [
                f"{m.get('name') or m['email']} <{m['email']}>"
                for m in db.list_org_members(user["org_id"])[:50]
                if m.get("email")
            ]
        except Exception:  # noqa: BLE001
            roster = []
        if roster:
            system += (
                "\n\nTeammates in this organization (name <email>). When the user names "
                "one, use that person's email as the recipient:\n- " + "\n- ".join(roster)
            )

    # Offer only tools THIS user has linked, so the agent never proposes an
    # action it can't carry out. Identity = the logged-in user, so the action
    # later runs as *them* (their own scoped token), never a shared account.
    identity = user["user_id"]
    active = {
        c for c in tools.CONNECTION_NAMES
        if (db.get_connected_account(identity, c) or {}).get("status") == "active"
    }
    specs = tools.specs_for(active)
    if specs:
        system += (
            "\n\nYou can take actions with the available tools. Call one ONLY when the user "
            "clearly asks you to do something (file an issue, send an email). The user confirms "
            "before anything runs, so propose the action when it's warranted; otherwise just answer."
        )
        repos = _github_repos_for_agent(agent)
        if repos:
            system += (
                "\n\nFor GitHub issues, file into a repository this agent already has context on: "
                f"{', '.join(repos)}. Set the tool's 'repo' to the most relevant 'owner/name'."
            )
        result = await llm.acomplete_with_tools(system, body.message, specs, model=config.LLM_BRIEF_MODEL)
        if result["tool_calls"]:
            call = result["tool_calls"][0]
            summary = tools.summarize(call["name"], call["arguments"])
            action = db.create_action(
                identity=identity,
                tool=call["name"],
                args=json.dumps(call["arguments"]),
                summary=summary,
                agent_id=agent["id"],
            )
            return {"proposed_action": {
                "id": action["id"],
                "tool": call["name"],
                "args": call["arguments"],
                "summary": summary,
            }}
        if result["content"]:
            return {"answer": result["content"], "context_used": [m.get("kind") for _, m in context]}

    answer = await llm.agenerate(system, body.message, model=config.LLM_BRIEF_MODEL)
    return {"answer": answer, "context_used": [m.get("kind") for _, m in context]}


def _chat_system(agent: dict, context: list) -> str:
    ctx_text = "\n\n".join(f"[{m.get('kind')}] {d}" for d, m in context)
    base = agent.get("system_prompt") or "You are a helpful AI teammate for a software team."
    return (
        f"{base}\n\nAnswer using this context (connected knowledge sources + memory "
        f"of past meetings this agent attended):\n{ctx_text or '(no context found)'}"
    )


def _repo_slug(uri: str) -> str | None:
    """Extract 'owner/name' from a GitHub URL (source URI)."""
    u = (uri or "").strip().rstrip("/")
    if u.endswith(".git"):
        u = u[:-4]
    marker = "github.com/"
    i = u.find(marker)
    if i == -1:
        return None
    parts = u[i + len(marker):].split("/")
    return f"{parts[0]}/{parts[1]}" if len(parts) >= 2 and parts[0] and parts[1] else None


def _github_repos_for_agent(agent: dict) -> list[str]:
    """The repos this agent has context on, from its connected GitHub sources.
    These drive where issues get filed — no hardcoded default repo needed."""
    repos: list[str] = []
    for sid in agent.get("source_ids", []):
        s = db.get_source(sid)
        if s and s.get("type") == "github":
            slug = _repo_slug(s.get("uri", ""))
            if slug and slug not in repos:
                repos.append(slug)
    return repos


@app.post("/api/chat/stream")
async def chat_stream(body: ChatIn, user: dict = Depends(auth.require_org)):
    """Same as /api/chat but streams the answer as plain-text deltas."""
    agent = db.get_agent(body.agent_id)
    if not agent:
        return JSONResponse({"error": "agent not found"}, status_code=404)
    context = await asyncio.to_thread(
        vectorstore.retrieve, body.message, agent.get("source_ids", []), agent["id"], 8
    )
    system = _chat_system(agent, context)

    async def gen():
        async for delta in llm.astream(system, body.message, model=config.LLM_BRIEF_MODEL):
            yield delta

    return StreamingResponse(
        gen(),
        media_type="text/plain; charset=utf-8",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ─── REST: Connections (Scalekit identities for taking actions) ──
KNOWN_CONNECTORS = [
    {"key": "github", "label": "GitHub", "note": "File issues in your org repos."},
    {"key": "gmail", "label": "Gmail", "note": "Send follow-up emails."},
]


@app.get("/api/connections")
async def list_connections(user: dict = Depends(auth.require_org)):
    """This user's connector links + whether Scalekit is configured at all."""
    identity = user["user_id"]
    out = []
    for c in KNOWN_CONNECTORS:
        acc = db.get_connected_account(identity, c["key"])
        out.append({**c, "status": acc["status"] if acc else "not_connected"})
    return {
        "identity": identity,
        "scalekit_configured": scalekit_client.configured(),
        "connections": out,
    }


@app.post("/api/connections/{connector}/authorize")
async def authorize_connection(connector: str, user: dict = Depends(auth.require_org)):
    """Create the user's connected account and return an OAuth link if not active."""
    conn_name = tools.CONNECTION_NAMES.get(connector)
    if not conn_name:
        return JSONResponse({"error": "unknown connector"}, status_code=404)
    identity = user["user_id"]
    tenant = user.get("org_id") or config.DEFAULT_TENANT_ID
    try:
        status = await asyncio.to_thread(scalekit_client.ensure_account, conn_name, identity)
        norm = "active" if status == "ACTIVE" else "pending"
        link = None
        if norm != "active":
            link = await asyncio.to_thread(scalekit_client.authorization_link, conn_name, identity)
        db.upsert_connected_account(identity, connector, norm, tenant)
        return {"status": norm, "link": link}
    except scalekit_client.ScalekitNotConfigured as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:  # noqa: BLE001
        log.exception("authorize failed connector=%s", connector)
        return JSONResponse({"error": str(e)}, status_code=502)


@app.get("/api/connections/{connector}")
async def connection_status(connector: str, user: dict = Depends(auth.require_org)):
    """Re-check status from Scalekit (used to poll after the OAuth popup)."""
    conn_name = tools.CONNECTION_NAMES.get(connector)
    if not conn_name:
        return JSONResponse({"error": "unknown connector"}, status_code=404)
    identity = user["user_id"]
    tenant = user.get("org_id") or config.DEFAULT_TENANT_ID
    try:
        status = await asyncio.to_thread(scalekit_client.account_status, conn_name, identity)
        norm = "active" if status == "ACTIVE" else "pending"
        db.upsert_connected_account(identity, connector, norm, tenant)
        return {"status": norm}
    except scalekit_client.ScalekitNotConfigured as e:
        return JSONResponse({"status": "not_connected", "error": str(e)})
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"status": "error", "error": str(e)})


@app.delete("/api/connections/{connector}")
async def disconnect_connection(connector: str, user: dict = Depends(auth.require_org)):
    """Drop this user's connected account for a connector (Scalekit + local), so
    they can reconnect as a different account/repo."""
    conn_name = tools.CONNECTION_NAMES.get(connector)
    if not conn_name:
        return JSONResponse({"error": "unknown connector"}, status_code=404)
    identity = user["user_id"]
    try:
        await asyncio.to_thread(scalekit_client.disconnect, conn_name, identity)
    except scalekit_client.ScalekitNotConfigured as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:  # noqa: BLE001
        log.exception("disconnect failed connector=%s", connector)
        return JSONResponse({"error": str(e)}, status_code=502)
    db.delete_connected_account(identity, connector)
    return {"status": "not_connected"}


# ─── REST: Actions (propose → confirm → audit) ────────────────
@app.get("/api/actions")
async def get_actions(user: dict = Depends(auth.require_org)):
    return db.list_actions()


@app.post("/api/actions/{action_id}/confirm")
async def confirm_action(action_id: str, user: dict = Depends(auth.require_org)):
    """Execute a previously-proposed action AS the user who owns it, and record it.
    Only that same user can confirm — you can't run an action as someone else."""
    action = db.get_action(action_id)
    if not action:
        return JSONResponse({"error": "not found"}, status_code=404)
    if action["identity"] != user["user_id"]:
        return JSONResponse({"error": "this action belongs to another user"}, status_code=403)
    if action["status"] != "proposed":
        return JSONResponse({"error": f"action already {action['status']}"}, status_code=409)
    args = json.loads(action["args"] or "{}")
    result = await tools.execute(action["tool"], args, action["identity"])
    status = "done" if result.get("ok") else "error"
    finished = db.finish_action(action_id, status, json.dumps(result))
    return {"ok": bool(result.get("ok")), "status": status, "result": result, "action": finished}


@app.post("/api/actions/{action_id}/cancel")
async def cancel_action(action_id: str, user: dict = Depends(auth.require_org)):
    action = db.get_action(action_id)
    if not action:
        return JSONResponse({"error": "not found"}, status_code=404)
    if action["identity"] != user["user_id"]:
        return JSONResponse({"error": "this action belongs to another user"}, status_code=403)
    db.finish_action(action_id, "cancelled", "")
    return {"ok": True}


# ─── Web UI: public landing at "/", the app at "/app" ─────────
_WEB_DIR = os.path.join(os.path.dirname(__file__), "..", "web")


@app.get("/")
async def landing_page():
    return FileResponse(os.path.join(_WEB_DIR, "landing.html"))


def _asset_version(name: str) -> str:
    """File mtime, used to cache-bust asset URLs so browsers refetch app.js /
    style.css the moment they change instead of serving a stale copy."""
    try:
        return str(int(os.path.getmtime(os.path.join(_WEB_DIR, name))))
    except OSError:
        return "0"


@app.get("/app")
async def app_page():
    # Serve index.html with mtime-stamped asset URLs. StaticFiles ignores the
    # query string (so the same file is served), but the browser treats a changed
    # ?v= as a new URL and refetches — no more "I don't see my change" caching.
    with open(os.path.join(_WEB_DIR, "index.html"), encoding="utf-8") as f:
        html = f.read()
    for asset in ("style.css", "app.js", "auth.js"):
        html = html.replace(f'/{asset}"', f'/{asset}?v={_asset_version(asset)}"')
    return HTMLResponse(html)


# Static assets (app.js, auth.js, style.css, landing.html). Registered LAST so
# the explicit "/" and "/app" routes above win.
if os.path.isdir(_WEB_DIR):
    app.mount("/", StaticFiles(directory=_WEB_DIR, html=True), name="web")
