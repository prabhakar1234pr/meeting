"""Action tools the agent can take — connector-agnostic registry.

Each tool = an OpenAI function schema + an async executor that runs AS a given
identity, fetching a fresh scoped token from Scalekit right before the call.
The LLM only proposes a tool; nothing runs until a human confirms (see the
/api/actions confirm flow in main.py).

Add a connector by appending to TOOLS. Availability is gated on the identity
having an ACTIVE connected account for that connector.
"""
import asyncio
import base64
import logging
from email.message import EmailMessage

import requests

from . import config, scalekit_client

log = logging.getLogger("teammate.tools")


# ─── executors ────────────────────────────────────────────────
async def _github_create_issue(args: dict, identity: str) -> dict:
    repo = (args.get("repo") or config.GITHUB_DEFAULT_REPO or "").strip()
    if "/" not in repo:
        return {"ok": False, "error": "Need a repo as 'owner/name' (or set GITHUB_DEFAULT_REPO)."}
    token = await asyncio.to_thread(
        scalekit_client.access_token, config.SCALEKIT_CONNECTION_GITHUB, identity,
    )
    payload = {
        "title": args.get("title"),
        "body": args.get("body"),
        "labels": args.get("labels"),
        "assignees": args.get("assignees"),
    }
    payload = {k: v for k, v in payload.items() if v}

    def _call():
        return requests.post(
            f"https://api.github.com/repos/{repo}/issues",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
            },
            json=payload,
            timeout=20,
        )

    r = await asyncio.to_thread(_call)
    if r.status_code >= 300:
        return {"ok": False, "error": f"GitHub {r.status_code}: {r.text[:300]}"}
    data = r.json()
    return {
        "ok": True,
        "summary": f"Created issue #{data.get('number')} in {repo}",
        "url": data.get("html_url"),
    }


async def _gmail_send_email(args: dict, identity: str) -> dict:
    to = (args.get("to") or "").strip()
    if not to:
        return {"ok": False, "error": "Need a recipient email address."}
    token = await asyncio.to_thread(
        scalekit_client.access_token, config.SCALEKIT_CONNECTION_GMAIL, identity,
    )
    msg = EmailMessage()
    msg["To"] = to
    msg["Subject"] = args.get("subject", "")
    msg.set_content(args.get("body", ""))
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()

    def _call():
        return requests.post(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
            headers={"Authorization": f"Bearer {token}"},
            json={"raw": raw},
            timeout=20,
        )

    r = await asyncio.to_thread(_call)
    if r.status_code >= 300:
        return {"ok": False, "error": f"Gmail {r.status_code}: {r.text[:300]}"}
    return {"ok": True, "summary": f"Email sent to {to}"}


# ─── registry ─────────────────────────────────────────────────
TOOLS = {
    "github_create_issue": {
        "connector": "github",
        "execute": _github_create_issue,
        "summarize": lambda a: (
            f"Create a GitHub issue in "
            f"{a.get('repo') or config.GITHUB_DEFAULT_REPO or 'the default repo'}: "
            f"“{a.get('title', '')}”"
        ),
        "spec": {
            "type": "function",
            "function": {
                "name": "github_create_issue",
                "description": "File a GitHub issue in an organization repo. Use when "
                               "the conversation identifies a bug, task, or follow-up to track.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "repo": {"type": "string", "description": "owner/name, e.g. acme/backend. Omit to use the default repo."},
                        "title": {"type": "string", "description": "Concise issue title."},
                        "body": {"type": "string", "description": "Issue body in Markdown: context, repro, acceptance."},
                        "labels": {"type": "array", "items": {"type": "string"}},
                        "assignees": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["title", "body"],
                },
            },
        },
    },
    "gmail_send_email": {
        "connector": "gmail",
        "execute": _gmail_send_email,
        "summarize": lambda a: f"Send an email to {a.get('to', '')} — “{a.get('subject', '')}”",
        "spec": {
            "type": "function",
            "function": {
                "name": "gmail_send_email",
                "description": "Send an email on the user's behalf. Use for follow-ups, summaries, or intros.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "to": {"type": "string", "description": "Recipient email address."},
                        "subject": {"type": "string"},
                        "body": {"type": "string", "description": "Plain-text email body."},
                    },
                    "required": ["to", "subject", "body"],
                },
            },
        },
    },
}

CONNECTION_NAMES = {
    "github": config.SCALEKIT_CONNECTION_GITHUB,
    "gmail": config.SCALEKIT_CONNECTION_GMAIL,
}


def specs_for(active_connectors) -> list[dict]:
    """OpenAI tool specs for the connectors the identity has actually linked."""
    active = set(active_connectors)
    return [t["spec"] for t in TOOLS.values() if t["connector"] in active]


def summarize(name: str, args: dict) -> str:
    tool = TOOLS.get(name)
    if not tool:
        return f"Run {name}"
    try:
        return tool["summarize"](args)
    except Exception:
        return f"Run {name}"


async def execute(name: str, args: dict, identity: str) -> dict:
    tool = TOOLS.get(name)
    if not tool:
        return {"ok": False, "error": f"Unknown tool: {name}"}
    try:
        return await tool["execute"](args, identity)
    except scalekit_client.ScalekitNotConfigured as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:  # noqa: BLE001 — surface any executor failure to the UI
        log.exception("tool %s failed", name)
        return {"ok": False, "error": str(e)}
