"""Scalekit AgentKit wrapper — the identity / scoped-token layer.

Generalizes gmail_agent.py: create a per-user connected account, run OAuth, and
fetch a *fresh* scoped access token right before an action runs, so the agent
acts AS a specific person, never as a shared service account.

Connection names must match the Scalekit Dashboard exactly. Gmail works with no
dashboard setup; other connectors (GitHub, Slack, CRM…) must be created once
under AgentKit → Connections.

Required env: SCALEKIT_ENVIRONMENT_URL, SCALEKIT_CLIENT_ID, SCALEKIT_CLIENT_SECRET.
"""
import logging

from . import config

log = logging.getLogger("teammate.scalekit")

_client = None


class ScalekitNotConfigured(RuntimeError):
    """Raised when Scalekit creds/SDK are missing, so callers can surface a
    clear 'connect your account' message instead of a 500."""


class ReauthRequired(RuntimeError):
    """A connected account's token expired or was revoked (common with Google,
    whose access tokens are short-lived). The user must reconnect the account."""


def configured() -> bool:
    return bool(
        config.SCALEKIT_CLIENT_ID
        and config.SCALEKIT_CLIENT_SECRET
        and config.SCALEKIT_ENVIRONMENT_URL
    )


def _actions():
    """Lazily build the Scalekit client (import + creds checked here so a
    missing SDK never crashes app import)."""
    global _client
    if not configured():
        raise ScalekitNotConfigured(
            "Set SCALEKIT_ENVIRONMENT_URL, SCALEKIT_CLIENT_ID and "
            "SCALEKIT_CLIENT_SECRET in .env to enable actions."
        )
    if _client is None:
        try:
            from scalekit import ScalekitClient
        except ImportError as e:
            raise ScalekitNotConfigured(
                "scalekit-sdk-python is not installed (pip install -r requirements.txt)."
            ) from e
        _client = ScalekitClient(
            client_id=config.SCALEKIT_CLIENT_ID,
            client_secret=config.SCALEKIT_CLIENT_SECRET,
            env_url=config.SCALEKIT_ENVIRONMENT_URL,
        )
    return _client.actions


def ensure_account(connection_name: str, identifier: str) -> str:
    """Create the connected account if needed; return its status
    ('ACTIVE' once the user has authorized). If one already exists, return the
    existing account's status — some environments return RESOURCE_ALREADY_EXISTS
    from get-or-create instead of handing back the existing record."""
    try:
        resp = _actions().get_or_create_connected_account(
            connection_name=connection_name, identifier=identifier,
        )
        return resp.connected_account.status
    except ScalekitNotConfigured:
        raise
    except Exception as e:  # noqa: BLE001
        if "already exist" in str(e).lower() or "RESOURCE_ALREADY_EXISTS" in str(e):
            return account_status(connection_name, identifier)
        raise


def account_status(connection_name: str, identifier: str) -> str:
    resp = _actions().get_connected_account(
        connection_name=connection_name, identifier=identifier,
    )
    return resp.connected_account.status


def authorization_link(connection_name: str, identifier: str) -> str:
    resp = _actions().get_authorization_link(
        connection_name=connection_name, identifier=identifier,
    )
    return resp.link


def disconnect(connection_name: str, identifier: str) -> None:
    """Delete this identity's connected account for a connector, so the user can
    reconnect as a different account/repo. Idempotent: an already-gone account
    (RESOURCE_NOT_FOUND) is treated as success."""
    try:
        _actions().delete_connected_account(
            connection_name=connection_name, identifier=identifier,
        )
    except ScalekitNotConfigured:
        raise
    except Exception as e:  # noqa: BLE001
        if "not found" in str(e).lower() or "RESOURCE_NOT_FOUND" in str(e):
            return
        raise


def access_token(connection_name: str, identifier: str) -> str:
    """Fetch a fresh OAuth access token for this identity + connector. Called
    right before every action so the token is never stale."""
    resp = _actions().get_connected_account(
        connection_name=connection_name, identifier=identifier,
    )
    details = resp.connected_account.authorization_details or {}
    token = details.get("oauth_token", {})
    at = token.get("access_token")
    if not at:
        raise ScalekitNotConfigured(
            f"No access token for '{connection_name}' as '{identifier}'. "
            "Authorize the connection first."
        )
    return at


def execute_tool(tool_name: str, tool_input: dict, identifier: str,
                 connection_name: str) -> dict:
    """Run an AgentKit tool server-side AS `identifier`, and return its `data`.

    This SDK version never hands back the raw OAuth token (`authorization_details`
    is empty) — Scalekit keeps the scoped token, makes the third-party API call
    itself, and returns the result. So actions go through here, not a local
    `requests` call with a fetched token."""
    try:
        resp = _actions().execute_tool(
            tool_name=tool_name,
            tool_input=tool_input,
            identifier=identifier,
            connection_name=connection_name,
        )
    except (ScalekitNotConfigured, ReauthRequired):
        raise
    except Exception as e:  # noqa: BLE001
        m = str(e).lower()
        if "reauthentication" in m or "token expired" in m or "unauthenticated" in m:
            raise ReauthRequired(
                f"Your '{connection_name}' connection expired. Reconnect it on the "
                "Connections page (Reauthorize), then try again."
            ) from e
        raise
    d = resp.model_dump() if hasattr(resp, "model_dump") else resp
    return d.get("data", d) if isinstance(d, dict) else {"result": d}
