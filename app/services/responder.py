"""Live in-meeting voice responder — one per meeting.

Reads the meeting's transcript from the Redis bus, and when the agent is
addressed by its wake phrase (or name), does RAG over the agent's connected
sources + past meetings, streams an OpenAI answer, and speaks it back through the
MeetStream audio bridge sentence-by-sentence so the first words land in ~1s.
"""
import asyncio
import json
import logging
import re

from .. import audio, bus, config, db, llm, meetstream, tools, tts, vectorstore

log = logging.getLogger("teammate.responder")

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_CHUNK_SAMPLES = int(config.MEETSTREAM_AUDIO_RATE * 0.8)  # ~0.8s per sendaudio frame
_NORM_RE = re.compile(r"[^a-z0-9]+")


def _norm(s: str) -> str:
    """Lowercase and collapse all punctuation/whitespace to single spaces, so
    'Hey Bora' matches transcripts like 'Hey, Bora.'."""
    return _NORM_RE.sub(" ", (s or "").lower()).strip()


# ElevenLabs only reliably supports <break> among SSML tags; anything else
# (<speak>, <prosody>, …) would be read aloud, so we strip it.
_NON_BREAK_TAG = re.compile(r"<(?!break\b)[^>]*>", re.IGNORECASE)
_ANY_TAG = re.compile(r"<[^>]+>")

# Only bother with a tool-decision when the ask sounds action-y, so normal Q&A
# stays fast (no extra LLM round-trip).
_ACTIONY = re.compile(r"\b(e-?mail|mail|send|message|issue|ticket|file|create|open|draft|invite)\b", re.I)
_CONFIRM_WORDS = ("confirm", "yes", "go ahead", "do it", "send it", "sure", "please do")
_CANCEL_WORDS = ("cancel", "stop", "never mind", "nevermind", "forget it", "no thanks")


def _tts_text(s: str) -> str:
    """Keep <break> tags for pacing, drop every other tag, for ElevenLabs."""
    return _NON_BREAK_TAG.sub("", s or "").strip()


def _display_text(s: str) -> str:
    """Strip all tags for the in-meeting chat message."""
    return re.sub(r"\s+", " ", _ANY_TAG.sub("", s or "")).strip()


class Responder:
    def __init__(self, bot_id: str, ws, agent: dict):
        self.bot_id = bot_id
        self.ws = ws
        self.agent = agent
        self.group = f"responder-{bot_id}"
        self.consumer = "r1"
        self.recent: list[str] = []          # rolling transcript context
        self.speaking: asyncio.Task | None = None
        self.pending: dict | None = None     # action awaiting a spoken confirm
        # What the agent listens for: the standard wake word (works for every
        # agent), plus this agent's own wake phrase and name as extra triggers.
        seen, self.triggers = set(), []
        for w in (config.WAKE_WORD, agent.get("wake_phrase"), agent.get("name")):
            nw = _norm(w or "")
            if nw and nw not in seen:
                seen.add(nw)
                self.triggers.append(nw)

    async def run(self) -> None:
        log.info("responder started bot=%s agent=%s triggers=%s", self.bot_id, self.agent.get("name"), self.triggers)
        try:
            await bus.ensure_group(self.bot_id, self.group)
            while True:
                batch = await bus.read_group(self.bot_id, self.group, self.consumer)
                for msg_id, seg in batch:
                    try:
                        await self._handle(seg)
                    except Exception:
                        log.exception("error handling segment")
                    finally:
                        await bus.ack(self.bot_id, self.group, msg_id)
        except asyncio.CancelledError:
            log.info("responder stopped bot=%s", self.bot_id)
            raise
        except Exception:
            log.exception("responder crashed bot=%s", self.bot_id)

    async def _handle(self, seg: dict) -> None:
        text = (seg.get("text") or "").strip()
        eot = seg.get("end_of_turn")
        log.info("seg bot=%s eot=%s speaker=%s text=%r", self.bot_id, eot, seg.get("speaker"), text[:80])
        if not text or not eot:
            return
        self.recent.append(f"{seg.get('speaker') or 'Speaker'}: {text}")
        self.recent = self.recent[-12:]

        # An action is waiting on a spoken confirm/cancel.
        if self.pending is not None:
            low = text.lower()
            if any(w in low for w in _CONFIRM_WORDS):
                await self._interrupt()
                self.speaking = asyncio.create_task(self._run_pending())
                return
            if any(w in low for w in _CANCEL_WORDS):
                self.pending = None
                await self._interrupt()
                self.speaking = asyncio.create_task(self._say("Okay, I won't."))
                return

        query = self._match_wake(text)
        if query is not None:
            log.info("WAKE matched bot=%s query=%r", self.bot_id, query)
            self.pending = None  # a fresh request supersedes any pending action
            await self._interrupt()  # barge-in over any current answer
            self.speaking = asyncio.create_task(self._speak(query, seg.get("speaker")))

    def _match_wake(self, text: str) -> str | None:
        """Return the query following any trigger phrase, or None if not
        addressed. Punctuation/spacing-insensitive."""
        nt = _norm(text)
        for nw in self.triggers:
            if nw in nt:
                after = nt[nt.find(nw) + len(nw):].strip()
                return after or nt
        return None

    async def _interrupt(self) -> None:
        if self.speaking and not self.speaking.done():
            self.speaking.cancel()
            try:
                await self.speaking
            except asyncio.CancelledError:
                pass
        await self._send(meetstream.interrupt_cmd(self.bot_id))

    async def _speak(self, query: str, speaker: str | None = None) -> None:
        try:
            # If it sounds like an action, try to propose a tool call first
            # (only when the operator actually has that connector linked).
            if _ACTIONY.search(query.lower()):
                identity, active, tenant = self._operator()
                specs = tools.specs_for(active) if active else []
                if specs and await self._propose(query, speaker, identity, tenant, specs):
                    return
            context = await asyncio.to_thread(
                vectorstore.retrieve, query, self.agent.get("source_ids", []),
                self.agent["id"], 6,
            )
            ctx_text = "\n\n".join(f"[{m.get('kind')}] {d}" for d, m in context)
            history = "\n".join(self.recent[-8:])
            system = self._build_system(ctx_text)
            prompt = (
                f"Live conversation so far:\n{history}\n\n"
                f'Someone just said to you: "{query}"\n\n'
                "Respond out loud now — briefly and naturally, as yourself."
            )
            n_src = sum(1 for _, m in context if m.get("kind") == "source")
            log.info("thinking bot=%s ctx=%d (src=%d mem=%d) query=%r",
                     self.bot_id, len(context), n_src, len(context) - n_src, query)

            buf = ""
            spoke = False
            async for tok in llm.astream(system, prompt, config.LLM_RESPONDER_MODEL, 0.4):
                buf += tok
                parts = _SENTENCE_END.split(buf)
                if len(parts) > 1:
                    *ready, buf = parts
                    for sentence in ready:
                        await self._say(sentence)
                        spoke = True
            if buf.strip():
                await self._say(buf)
                spoke = True
            if not spoke:
                log.warning("empty answer bot=%s", self.bot_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("speak failed bot=%s", self.bot_id)

    # ─── Actions (operator identity, voice-confirmed) ─────────────
    def _operator(self):
        """Whose connected account meeting actions run as. Demo policy: the
        first user with an active connection (a single operator)."""
        try:
            accts = [a for a in db.list_connected_accounts() if a.get("status") == "active"]
        except Exception:  # noqa: BLE001
            return None, set(), None
        if not accts:
            return None, set(), None
        identity = accts[0]["identifier"]
        tenant = accts[0].get("tenant_id")
        active = {a["connector"] for a in accts if a["identifier"] == identity}
        return identity, active, tenant

    def _roster(self, tenant):
        """Org teammates as 'Name <email>' so the agent can resolve recipients
        from a spoken name or 'me'."""
        if not tenant:
            return []
        try:
            return [f"{m.get('name') or m['email']} <{m['email']}>"
                    for m in db.list_org_members(tenant)[:50] if m.get("email")]
        except Exception:  # noqa: BLE001
            return []

    def _action_system(self, roster, speaker):
        name = self.agent.get("name") or "the assistant"
        who = f"The person who just spoke is {speaker}. " if speaker else ""
        r = ("\n\nTeammates (name <email>) — resolve any name or 'me' to one of these:\n- "
             + "\n- ".join(roster)) if roster else ""
        return (f"You are {name}, a teammate in a live meeting who can take real actions for "
                f"the team using the provided tools. {who}Call a tool ONLY when someone clearly "
                "asks you to do something it covers (send an email, file a GitHub issue). Fill "
                "every required field; resolve a recipient name or 'me' to an email below." + r)

    async def _propose(self, query, speaker, identity, tenant, specs) -> bool:
        """Ask the model whether this turn is an action. If so, stash it and ask
        for a spoken confirmation. Returns True when an action is now pending."""
        system = self._action_system(self._roster(tenant), speaker)
        prompt = ("Live conversation so far:\n" + "\n".join(self.recent[-8:])
                  + f'\n\n{speaker or "Someone"} said: "{query}"')
        result = await llm.acomplete_with_tools(system, prompt, specs, model=config.LLM_RESPONDER_MODEL)
        if not result["tool_calls"]:
            return False
        call = result["tool_calls"][0]
        summary = tools.summarize(call["name"], call["arguments"])
        meeting = db.get_meeting_by_bot(self.bot_id) or {}
        self.pending = {"tool": call["name"], "args": call["arguments"],
                        "identity": identity, "summary": summary, "meeting_id": meeting.get("id")}
        log.info("action proposed bot=%s %s", self.bot_id, summary)
        await self._say(f"{summary}. Say confirm to go ahead, or cancel.")
        return True

    async def _run_pending(self) -> None:
        action, self.pending = self.pending, None
        if not action:
            return
        log.info("action confirmed bot=%s %s", self.bot_id, action["summary"])
        try:
            result = await tools.execute(action["tool"], action["args"], action["identity"])
        except Exception as e:  # noqa: BLE001
            result = {"ok": False, "error": str(e)}
        try:  # record it in the Activity audit trail, same as chat actions
            rec = db.create_action(
                identity=action["identity"], tool=action["tool"],
                args=json.dumps(action["args"]), summary=action["summary"],
                agent_id=self.agent.get("id"), meeting_id=action.get("meeting_id"),
            )
            db.finish_action(rec["id"], "done" if result.get("ok") else "error", json.dumps(result))
        except Exception:  # noqa: BLE001
            log.exception("action audit failed bot=%s", self.bot_id)
        if result.get("ok"):
            await self._say(f"Done. {result.get('summary', '')}")
        else:
            await self._say(f"Sorry, that didn't work. {result.get('error', '')}")

    def _build_system(self, ctx_text: str) -> str:
        name = self.agent.get("name") or "the assistant"
        persona = (self.agent.get("system_prompt") or "").strip()
        return (
            f"You are {name}, a real member of this live meeting — a human-sounding teammate "
            f"who is present in the call and speaking out loud."
            + (f" {persona}" if persona else "")
            + "\n\nHow you speak:\n"
            "- Talk like a person in the room: first person, warm, natural, conversational.\n"
            "- Answer directly what was just said. Never narrate your thinking, never repeat the "
            "question back, never refer to yourself in the third person, and never say things like "
            "'let me see if I know', 'as an AI', or 'I'm an assistant'.\n"
            "- Be brief — usually 1–2 spoken sentences. No lists, no markdown, no headings.\n"
            "- If you don't know, or it isn't in what you have, just say so casually ('honestly, "
            "I'm not sure', 'I don't have that in front of me') and offer to check if it'd help.\n"
            "- Only state facts about this team's code, docs, or past meetings if they appear in the "
            "context below. Don't invent details.\n\n"
            "This text is read aloud by a speech engine. Write plain spoken sentences, and add "
            '<break time="0.3s"/> where a natural pause helps between thoughts. Use no other tags.\n\n'
            f"What you know (retrieved context):\n"
            f"{ctx_text or '(nothing specific retrieved — answer from the conversation, or say you dont have it)'}"
        )

    async def _say(self, sentence: str) -> None:
        speech = _tts_text(sentence)   # keeps <break> tags for ElevenLabs
        display = _display_text(sentence)
        if not speech:
            return
        if display:
            await self._send(meetstream.sendchat_cmd(self.bot_id, display, is_final=True))
        pcm = await asyncio.to_thread(tts.synthesize, speech)
        pcm = audio.resample(pcm, config.ELEVENLABS_OUTPUT_RATE, config.MEETSTREAM_AUDIO_RATE)
        n = 0
        for ch in audio.chunks(pcm, _CHUNK_SAMPLES):
            await self._send(meetstream.sendaudio_cmd(self.bot_id, ch, config.MEETSTREAM_AUDIO_RATE))
            n += 1
            await asyncio.sleep(0.7)  # pace just under real-time
        log.info("said bot=%s chars=%d pcm_bytes=%d chunks=%d", self.bot_id, len(display), len(pcm), n)

    async def _send(self, cmd: dict) -> None:
        try:
            await self.ws.send_text(json.dumps(cmd))
        except Exception:
            log.exception("send failed bot=%s cmd=%s", self.bot_id, cmd.get("command"))
