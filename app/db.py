"""SQLite metadata store: knowledge sources, agents, meetings, briefs.

Vectors live in Chroma (see vectorstore.py); this holds the relational metadata
and the many-to-many link between agents and knowledge sources.
"""
import contextlib
import os
import sqlite3
import time
import uuid

from . import config


def _now() -> int:
    return int(time.time())


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


@contextlib.contextmanager
def get_conn():
    d = os.path.dirname(config.DB_PATH)
    if d:
        os.makedirs(d, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    type        TEXT NOT NULL,              -- github | file
    uri         TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',  -- pending|ingesting|ready|error
    chunk_count INTEGER NOT NULL DEFAULT 0,
    error       TEXT,
    tenant_id   TEXT NOT NULL DEFAULT 'local',
    created_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS agents (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    system_prompt TEXT NOT NULL DEFAULT '',
    wake_phrase   TEXT NOT NULL DEFAULT '',
    voice         TEXT,
    tenant_id     TEXT NOT NULL DEFAULT 'local',
    created_at    INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_sources (
    agent_id  TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    PRIMARY KEY (agent_id, source_id)
);

CREATE TABLE IF NOT EXISTS meetings (
    id            TEXT PRIMARY KEY,
    agent_id      TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    bot_id        TEXT UNIQUE,
    transcript_id TEXT,
    meeting_link  TEXT NOT NULL,
    title         TEXT,
    status        TEXT NOT NULL DEFAULT 'created',
    started_at    INTEGER,
    ended_at      INTEGER
);

CREATE TABLE IF NOT EXISTS briefs (
    id              TEXT PRIMARY KEY,
    meeting_id      TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    brief           TEXT,
    notes           TEXT,
    transcript_path TEXT,
    created_at      INTEGER NOT NULL
);

-- Scalekit connected accounts: which identity is linked to which connector.
CREATE TABLE IF NOT EXISTS connected_accounts (
    id          TEXT PRIMARY KEY,
    identifier  TEXT NOT NULL,              -- the "acts as" user id (operator for now)
    connector   TEXT NOT NULL,              -- gmail | github | ...
    status      TEXT NOT NULL DEFAULT 'pending',  -- pending | active | error
    tenant_id   TEXT NOT NULL DEFAULT 'local',
    created_at  INTEGER NOT NULL,
    UNIQUE (identifier, connector)
);

-- Audit trail of every action the agent proposed / took, and as whom.
CREATE TABLE IF NOT EXISTS actions (
    id          TEXT PRIMARY KEY,
    agent_id    TEXT,
    meeting_id  TEXT,
    identity    TEXT NOT NULL,              -- who the action ran as
    tool        TEXT NOT NULL,
    args        TEXT NOT NULL,              -- JSON
    summary     TEXT,                       -- human-readable proposal
    status      TEXT NOT NULL DEFAULT 'proposed',  -- proposed|done|error|cancelled
    result      TEXT,                       -- JSON/text outcome
    created_at  INTEGER NOT NULL,
    executed_at INTEGER
);

-- ─── Auth: users, orgs, memberships, sessions ─────────────────
CREATE TABLE IF NOT EXISTS users (
    id               TEXT PRIMARY KEY,
    scalekit_user_id TEXT UNIQUE,
    email            TEXT,
    name             TEXT,
    created_at       INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS orgs (
    id              TEXT PRIMARY KEY,
    scalekit_org_id TEXT,
    name            TEXT NOT NULL,
    created_by      TEXT,
    created_at      INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS memberships (
    user_id    TEXT NOT NULL,
    org_id     TEXT NOT NULL,
    role       TEXT NOT NULL DEFAULT 'member',   -- admin | member
    status     TEXT NOT NULL DEFAULT 'active',   -- active | pending
    created_at INTEGER NOT NULL,
    PRIMARY KEY (user_id, org_id)
);

CREATE TABLE IF NOT EXISTS sessions (
    sid           TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL,
    access_token  TEXT,
    refresh_token TEXT,
    id_token      TEXT,
    active_org_id TEXT,
    expires_at    INTEGER NOT NULL
);
"""


def _migrate(c) -> None:
    """Additive migrations for DBs created before a column existed."""
    cols = {r["name"] for r in c.execute("PRAGMA table_info(meetings)").fetchall()}
    if "org_id" not in cols:
        c.execute("ALTER TABLE meetings ADD COLUMN org_id TEXT")


def init_db() -> None:
    with get_conn() as c:
        c.executescript(SCHEMA)
        _migrate(c)


# ─── Sources (Knowledge Store) ────────────────────────────────
def create_source(name: str, type: str, uri: str, tenant_id: str) -> dict:
    sid = _id("src")
    with get_conn() as c:
        c.execute(
            "INSERT INTO sources (id, name, type, uri, status, tenant_id, created_at)"
            " VALUES (?,?,?,?, 'pending', ?, ?)",
            (sid, name, type, uri, tenant_id, _now()),
        )
    return get_source(sid)


def set_source_status(source_id: str, status: str, chunk_count=None, error=None) -> None:
    with get_conn() as c:
        c.execute(
            "UPDATE sources SET status=?,"
            " chunk_count=COALESCE(?, chunk_count), error=? WHERE id=?",
            (status, chunk_count, error, source_id),
        )


def get_source(source_id: str):
    with get_conn() as c:
        row = c.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
    return dict(row) if row else None


def delete_source(source_id: str) -> None:
    """Delete a source. agent_sources links cascade via the foreign key."""
    with get_conn() as c:
        c.execute("DELETE FROM sources WHERE id=?", (source_id,))


def list_sources(tenant_id: str | None = None) -> list[dict]:
    with get_conn() as c:
        if tenant_id:
            rows = c.execute(
                "SELECT * FROM sources WHERE tenant_id=? ORDER BY created_at DESC",
                (tenant_id,),
            ).fetchall()
        else:
            rows = c.execute("SELECT * FROM sources ORDER BY created_at DESC").fetchall()
    return [dict(r) for r in rows]


# ─── Agents ───────────────────────────────────────────────────
def create_agent(name, system_prompt, wake_phrase, voice, tenant_id) -> dict:
    aid = _id("agt")
    with get_conn() as c:
        c.execute(
            "INSERT INTO agents (id, name, system_prompt, wake_phrase, voice,"
            " tenant_id, created_at) VALUES (?,?,?,?,?,?,?)",
            (aid, name, system_prompt, wake_phrase, voice, tenant_id, _now()),
        )
    return get_agent(aid)


def get_agent(agent_id: str):
    with get_conn() as c:
        row = c.execute("SELECT * FROM agents WHERE id=?", (agent_id,)).fetchone()
        if not row:
            return None
        agent = dict(row)
        src = c.execute(
            "SELECT source_id FROM agent_sources WHERE agent_id=?", (agent_id,)
        ).fetchall()
    agent["source_ids"] = [r["source_id"] for r in src]
    return agent


def list_agents(tenant_id: str | None = None) -> list[dict]:
    with get_conn() as c:
        if tenant_id:
            rows = c.execute(
                "SELECT * FROM agents WHERE tenant_id=? ORDER BY created_at DESC",
                (tenant_id,),
            ).fetchall()
        else:
            rows = c.execute("SELECT * FROM agents ORDER BY created_at DESC").fetchall()
        agents = [dict(r) for r in rows]
        for a in agents:
            src = c.execute(
                "SELECT source_id FROM agent_sources WHERE agent_id=?", (a["id"],)
            ).fetchall()
            a["source_ids"] = [r["source_id"] for r in src]
    return agents


def update_agent(agent_id, name=None, system_prompt=None, wake_phrase=None, voice=None):
    """Patch the given fields (None = leave unchanged); returns the fresh agent."""
    fields = {
        "name": name,
        "system_prompt": system_prompt,
        "wake_phrase": wake_phrase,
        "voice": voice,
    }
    fields = {k: v for k, v in fields.items() if v is not None}
    if fields:
        assignments = ", ".join(f"{k}=?" for k in fields)
        with get_conn() as c:
            c.execute(
                f"UPDATE agents SET {assignments} WHERE id=?",
                (*fields.values(), agent_id),
            )
    return get_agent(agent_id)


def set_agent_sources(agent_id: str, source_ids: list[str]) -> None:
    """Replace the agent's connected sources with the given set."""
    with get_conn() as c:
        c.execute("DELETE FROM agent_sources WHERE agent_id=?", (agent_id,))
        c.executemany(
            "INSERT OR IGNORE INTO agent_sources (agent_id, source_id) VALUES (?,?)",
            [(agent_id, s) for s in source_ids],
        )


# ─── Meetings ─────────────────────────────────────────────────
def create_meeting(agent_id, meeting_link, title, bot_id=None, transcript_id=None,
                   org_id=None) -> dict:
    mid = _id("mtg")
    with get_conn() as c:
        c.execute(
            "INSERT INTO meetings (id, agent_id, bot_id, transcript_id, meeting_link,"
            " title, status, started_at, org_id) VALUES (?,?,?,?,?,?, 'created', ?, ?)",
            (mid, agent_id, bot_id, transcript_id, meeting_link, title, _now(), org_id),
        )
    return get_meeting(mid)


def get_meeting(meeting_id: str):
    with get_conn() as c:
        row = c.execute("SELECT * FROM meetings WHERE id=?", (meeting_id,)).fetchone()
    return dict(row) if row else None


def get_meeting_by_bot(bot_id: str):
    with get_conn() as c:
        row = c.execute("SELECT * FROM meetings WHERE bot_id=?", (bot_id,)).fetchone()
    return dict(row) if row else None


def update_meeting(meeting_id: str, **fields) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    with get_conn() as c:
        c.execute(f"UPDATE meetings SET {cols} WHERE id=?", (*fields.values(), meeting_id))


def begin_briefing(meeting_id: str) -> bool:
    """Atomically claim a meeting for finalization. Returns True only for the
    caller that flips it into 'briefing'; every later caller gets False. This is
    the run-once guard so the lifecycle webhook, the bridge disconnect, and
    startup recovery can all *try* to finalize the same meeting without producing
    duplicate briefs or racing."""
    with get_conn() as c:
        cur = c.execute(
            "UPDATE meetings SET status='briefing', ended_at=? "
            "WHERE id=? AND status NOT IN ('briefing','briefed','ended')",
            (_now(), meeting_id),
        )
        return cur.rowcount > 0


def meetings_in_progress() -> list[dict]:
    """Meetings still marked live/finalizing. At startup these are necessarily
    stale (a real bot can't survive a process restart), so they get recovered."""
    with get_conn() as c:
        rows = c.execute(
            "SELECT * FROM meetings WHERE status IN ('in_meeting','briefing')"
        ).fetchall()
    return [dict(r) for r in rows]


# ─── Briefs ───────────────────────────────────────────────────
def create_brief(meeting_id, brief, notes, transcript_path) -> dict:
    bid = _id("brf")
    with get_conn() as c:
        c.execute(
            "INSERT INTO briefs (id, meeting_id, brief, notes, transcript_path,"
            " created_at) VALUES (?,?,?,?,?,?)",
            (bid, meeting_id, brief, notes, transcript_path, _now()),
        )
        row = c.execute("SELECT * FROM briefs WHERE id=?", (bid,)).fetchone()
    return dict(row)


def list_briefs(org_id: str | None = None) -> list[dict]:
    where = "WHERE m.org_id = ?" if org_id else ""
    params = (org_id,) if org_id else ()
    with get_conn() as c:
        rows = c.execute(
            "SELECT b.*, m.title AS meeting_title, m.agent_id, a.name AS agent_name"
            " FROM briefs b JOIN meetings m ON b.meeting_id=m.id"
            f" JOIN agents a ON m.agent_id=a.id {where} ORDER BY b.created_at DESC",
            params,
        ).fetchall()
    return [dict(r) for r in rows]


def list_meetings(org_id: str | None = None) -> list[dict]:
    """All meetings with agent name and whether a brief exists (for the list)."""
    where = "WHERE m.org_id = ?" if org_id else ""
    params = (org_id,) if org_id else ()
    with get_conn() as c:
        rows = c.execute(
            "SELECT m.*, a.name AS agent_name,"
            " (SELECT COUNT(*) FROM briefs b WHERE b.meeting_id = m.id) AS brief_count"
            " FROM meetings m JOIN agents a ON m.agent_id = a.id"
            f" {where} ORDER BY m.started_at DESC",
            params,
        ).fetchall()
    return [dict(r) for r in rows]


def get_brief_for_meeting(meeting_id: str):
    with get_conn() as c:
        row = c.execute(
            "SELECT * FROM briefs WHERE meeting_id=? ORDER BY created_at DESC LIMIT 1",
            (meeting_id,),
        ).fetchone()
    return dict(row) if row else None


# ─── Connected accounts (Scalekit identities) ─────────────────
def upsert_connected_account(identifier: str, connector: str, status: str,
                             tenant_id: str = "local") -> dict:
    with get_conn() as c:
        row = c.execute(
            "SELECT * FROM connected_accounts WHERE identifier=? AND connector=?",
            (identifier, connector),
        ).fetchone()
        if row:
            c.execute(
                "UPDATE connected_accounts SET status=? WHERE id=?",
                (status, row["id"]),
            )
            cid = row["id"]
        else:
            cid = _id("ca")
            c.execute(
                "INSERT INTO connected_accounts (id, identifier, connector, status,"
                " tenant_id, created_at) VALUES (?,?,?,?,?,?)",
                (cid, identifier, connector, status, tenant_id, _now()),
            )
        out = c.execute("SELECT * FROM connected_accounts WHERE id=?", (cid,)).fetchone()
    return dict(out)


def get_connected_account(identifier: str, connector: str):
    with get_conn() as c:
        row = c.execute(
            "SELECT * FROM connected_accounts WHERE identifier=? AND connector=?",
            (identifier, connector),
        ).fetchone()
    return dict(row) if row else None


def delete_connected_account(identifier: str, connector: str) -> None:
    """Forget a connector link for this identity (used by Disconnect). Safe to
    call when no row exists."""
    with get_conn() as c:
        c.execute(
            "DELETE FROM connected_accounts WHERE identifier=? AND connector=?",
            (identifier, connector),
        )


def list_connected_accounts(tenant_id: str | None = None) -> list[dict]:
    with get_conn() as c:
        if tenant_id:
            rows = c.execute(
                "SELECT * FROM connected_accounts WHERE tenant_id=? ORDER BY created_at DESC",
                (tenant_id,),
            ).fetchall()
        else:
            rows = c.execute("SELECT * FROM connected_accounts ORDER BY created_at DESC").fetchall()
    return [dict(r) for r in rows]


# ─── Actions (audit trail) ────────────────────────────────────
def create_action(identity: str, tool: str, args: str, summary: str,
                  agent_id=None, meeting_id=None) -> dict:
    aid = _id("act")
    with get_conn() as c:
        c.execute(
            "INSERT INTO actions (id, agent_id, meeting_id, identity, tool, args,"
            " summary, status, created_at) VALUES (?,?,?,?,?,?,?, 'proposed', ?)",
            (aid, agent_id, meeting_id, identity, tool, args, summary, _now()),
        )
    return get_action(aid)


def get_action(action_id: str):
    with get_conn() as c:
        row = c.execute("SELECT * FROM actions WHERE id=?", (action_id,)).fetchone()
    return dict(row) if row else None


def finish_action(action_id: str, status: str, result: str) -> dict:
    with get_conn() as c:
        c.execute(
            "UPDATE actions SET status=?, result=?, executed_at=? WHERE id=?",
            (status, result, _now(), action_id),
        )
    return get_action(action_id)


def list_actions(limit: int = 50) -> list[dict]:
    with get_conn() as c:
        rows = c.execute(
            "SELECT ac.*, a.name AS agent_name FROM actions ac"
            " LEFT JOIN agents a ON ac.agent_id = a.id"
            " ORDER BY ac.created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


# ─── Auth: users ──────────────────────────────────────────────
def upsert_user(scalekit_user_id: str, email: str | None, name: str | None) -> dict:
    """Find a user by their Scalekit id (or create), refreshing email/name."""
    with get_conn() as c:
        row = c.execute(
            "SELECT * FROM users WHERE scalekit_user_id=?", (scalekit_user_id,)
        ).fetchone()
        if row:
            c.execute(
                "UPDATE users SET email=COALESCE(?, email), name=COALESCE(?, name)"
                " WHERE id=?",
                (email, name, row["id"]),
            )
            uid = row["id"]
        else:
            uid = _id("usr")
            c.execute(
                "INSERT INTO users (id, scalekit_user_id, email, name, created_at)"
                " VALUES (?,?,?,?,?)",
                (uid, scalekit_user_id, email, name, _now()),
            )
        row = c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    return dict(row)


def get_user(user_id: str):
    with get_conn() as c:
        row = c.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    return dict(row) if row else None


def get_user_by_scalekit(scalekit_user_id: str):
    with get_conn() as c:
        row = c.execute(
            "SELECT * FROM users WHERE scalekit_user_id=?", (scalekit_user_id,)
        ).fetchone()
    return dict(row) if row else None


# ─── Auth: orgs ───────────────────────────────────────────────
def create_org(name: str, scalekit_org_id: str | None, created_by: str) -> dict:
    oid = _id("org")
    with get_conn() as c:
        c.execute(
            "INSERT INTO orgs (id, scalekit_org_id, name, created_by, created_at)"
            " VALUES (?,?,?,?,?)",
            (oid, scalekit_org_id, name, created_by, _now()),
        )
        row = c.execute("SELECT * FROM orgs WHERE id=?", (oid,)).fetchone()
    return dict(row)


def get_org(org_id: str):
    with get_conn() as c:
        row = c.execute("SELECT * FROM orgs WHERE id=?", (org_id,)).fetchone()
    return dict(row) if row else None


# ─── Auth: memberships ────────────────────────────────────────
def add_membership(user_id: str, org_id: str, role: str = "member", status: str = "active") -> None:
    with get_conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO memberships (user_id, org_id, role, status, created_at)"
            " VALUES (?,?,?,?,?)",
            (user_id, org_id, role, status, _now()),
        )


def get_membership(user_id: str, org_id: str):
    with get_conn() as c:
        row = c.execute(
            "SELECT * FROM memberships WHERE user_id=? AND org_id=?", (user_id, org_id)
        ).fetchone()
    return dict(row) if row else None


def memberships_for_user(user_id: str) -> list[dict]:
    with get_conn() as c:
        rows = c.execute(
            "SELECT m.*, o.name AS org_name FROM memberships m"
            " JOIN orgs o ON m.org_id = o.id WHERE m.user_id=?"
            " ORDER BY m.created_at ASC",
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def activate_membership(user_id: str, org_id: str) -> None:
    with get_conn() as c:
        c.execute(
            "UPDATE memberships SET status='active' WHERE user_id=? AND org_id=?",
            (user_id, org_id),
        )


def list_org_members(org_id: str) -> list[dict]:
    with get_conn() as c:
        rows = c.execute(
            "SELECT m.role, m.status, u.email, u.name, u.id AS user_id"
            " FROM memberships m JOIN users u ON m.user_id = u.id"
            " WHERE m.org_id=? ORDER BY m.created_at ASC",
            (org_id,),
        ).fetchall()
    return [dict(r) for r in rows]


# ─── Auth: sessions ───────────────────────────────────────────
def create_session(user_id, access_token, refresh_token, id_token, active_org_id, ttl_seconds) -> str:
    sid = uuid.uuid4().hex + uuid.uuid4().hex
    with get_conn() as c:
        c.execute(
            "INSERT INTO sessions (sid, user_id, access_token, refresh_token, id_token,"
            " active_org_id, expires_at) VALUES (?,?,?,?,?,?,?)",
            (sid, user_id, access_token, refresh_token, id_token, active_org_id,
             _now() + int(ttl_seconds)),
        )
    return sid


def get_session(sid: str):
    with get_conn() as c:
        row = c.execute("SELECT * FROM sessions WHERE sid=?", (sid,)).fetchone()
    if not row:
        return None
    s = dict(row)
    if s["expires_at"] < _now():
        delete_session(sid)
        return None
    return s


def update_session(sid: str, **fields) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    with get_conn() as c:
        c.execute(f"UPDATE sessions SET {cols} WHERE sid=?", (*fields.values(), sid))


def delete_session(sid: str) -> None:
    with get_conn() as c:
        c.execute("DELETE FROM sessions WHERE sid=?", (sid,))
