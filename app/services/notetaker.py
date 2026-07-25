"""Post-meeting pipeline: transcript → brief + notes → store + embed to memory.

The transcript is reconstructed from the Redis bus (what we streamed live during
the meeting), so it works with streaming providers that produce no post-call
transcript artifact. Falls back to MeetStream's get_transcript if we ever have a
transcript_id.
"""
import asyncio
import logging
import os

from .. import bus, config, db, llm, meetstream, vectorstore
from ..ingest.chunk import chunk_text

log = logging.getLogger("teammate.notetaker")

_SYSTEM = "You write concise, useful post-meeting write-ups for a software engineering team."
_TRANSCRIPT_DIR = os.path.join("data", "transcripts")


def _format_ms(segments) -> str:
    """Format MeetStream's post-call transcript (list of segment dicts)."""
    if not isinstance(segments, list):
        return str(segments)
    lines = []
    for s in segments:
        if isinstance(s, dict):
            txt = s.get("transcript") or s.get("text") or ""
            if txt:
                lines.append(f"{s.get('speaker') or 'Speaker'}: {txt}")
    return "\n".join(lines)


async def _collect_transcript(meeting: dict) -> str:
    """Prefer the live transcript we streamed to the bus; fall back to MeetStream."""
    segments = await bus.read_all(meeting.get("bot_id") or "")
    lines = [
        f"{s.get('speaker') or 'Speaker'}: {s['text']}"
        for s in segments
        if s.get("end_of_turn") and (s.get("text") or "").strip()
    ]
    if lines:
        return "\n".join(lines)

    tid = meeting.get("transcript_id")
    if tid:
        try:
            return _format_ms(await meetstream.get_transcript(tid))
        except Exception:
            log.exception("get_transcript failed for %s", tid)
    return ""


async def _make_title(excerpt: str) -> str:
    """A short, specific title for a meeting, generated from its transcript."""
    t = await llm.agenerate(
        "You name meetings for a software team.",
        "Give a short, specific 3-6 word title for this meeting. "
        "No surrounding quotes, no trailing punctuation.\n\n" + excerpt,
        model=config.LLM_RESPONDER_MODEL,
        temperature=0.3,
    )
    return (t or "").strip().strip('"').splitlines()[0][:80] if t else ""


async def process(meeting: dict) -> None:
    mid = meeting["id"]
    log.info("notetaker start meeting=%s bot=%s", mid, meeting.get("bot_id"))
    try:
        transcript = await _collect_transcript(meeting)
        if not transcript.strip():
            log.warning("no transcript captured for meeting=%s", mid)
            db.update_meeting(mid, status="ended")
            return

        excerpt = transcript[:15000]

        # Name the meeting if the user didn't give it a title.
        if not (meeting.get("title") or "").strip():
            title = await _make_title(excerpt)
            if title:
                db.update_meeting(mid, title=title)
                meeting["title"] = title
                log.info("named meeting=%s title=%r", mid, title)

        os.makedirs(_TRANSCRIPT_DIR, exist_ok=True)
        path = os.path.join(_TRANSCRIPT_DIR, f"{mid}.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"# {meeting.get('title') or mid}\n\n{transcript}")
        brief = await llm.agenerate(
            _SYSTEM,
            f"Write a concise 5-bullet executive brief of this meeting:\n\n{excerpt}",
            model=config.LLM_BRIEF_MODEL,
        )
        notes = await llm.agenerate(
            _SYSTEM,
            "Write detailed meeting notes with sections: Decisions, Action Items "
            f"(with owners), Open Questions.\n\n{excerpt}",
            model=config.LLM_BRIEF_MODEL,
        )

        db.create_brief(mid, brief, notes, path)
        db.update_meeting(mid, status="briefed")

        memory = f"Meeting: {meeting.get('title') or mid}\n\nBrief:\n{brief}\n\nNotes:\n{notes}"
        await asyncio.to_thread(
            vectorstore.add_meeting_memory, mid, meeting["agent_id"], chunk_text(memory)
        )
        log.info("notetaker done meeting=%s brief_chars=%d", mid, len(brief or ""))
    except Exception:
        log.exception("notetaker failed meeting=%s", mid)
