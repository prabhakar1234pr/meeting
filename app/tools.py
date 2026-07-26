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
        return {"ok": False, "error": "Need a repo as 'owner/name'."}
    owner, name = repo.split("/", 1)
    tool_input = {"owner": owner, "repo": name, "title": args.get("title")}
    for k in ("body", "labels", "assignees"):
        if args.get(k):
            tool_input[k] = args[k]
    data = await asyncio.to_thread(
        scalekit_client.execute_tool, "github_issue_create", tool_input,
        identity, config.SCALEKIT_CONNECTION_GITHUB,
    )
    num = data.get("number")
    num = int(num) if isinstance(num, (int, float)) else num
    return {
        "ok": True,
        "summary": f"Created issue #{num} in {repo}",
        "url": data.get("html_url"),
    }


async def _gmail_send_email(args: dict, identity: str) -> dict:
    to = (args.get("to") or "").strip()
    if not to:
        return {"ok": False, "error": "Need a recipient email address."}
    tool_input = {"to": to, "subject": args.get("subject", ""), "body": args.get("body", "")}
    data = await asyncio.to_thread(
        scalekit_client.execute_tool, "gmail_send_message", tool_input,
        identity, config.SCALEKIT_CONNECTION_GMAIL,
    )
    mid = data.get("id") if isinstance(data, dict) else None
    return {"ok": True, "summary": f"Email sent to {to}", "id": mid}


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
    except scalekit_client.ReauthRequired as e:
        return {"ok": False, "error": str(e), "reauth": True}
    except scalekit_client.ScalekitNotConfigured as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:  # noqa: BLE001 — surface any executor failure to the UI
        log.exception("tool %s failed", name)
        return {"ok": False, "error": str(e)}
