"""LLM wrapper — OpenAI-compatible (async).

The rest of the app only imports `agenerate` / `astream` from here, so swapping
providers is a single-file change. The client points at `config.LLM_BASE_URL`
when set (e.g. Google Gemini's OpenAI-compatible endpoint), else OpenAI. Uses a
fast model for the live responder and a richer model for briefs (both env-tunable).
"""
from openai import AsyncOpenAI

from . import config

_client: AsyncOpenAI | None = None


def _get() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(
            api_key=config.LLM_API_KEY,
            base_url=config.LLM_BASE_URL or None,
        )
    return _client


def _messages(system: str, prompt: str) -> list[dict]:
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": prompt},
    ]


async def agenerate(system: str, prompt: str, model: str | None = None, temperature: float = 0.4) -> str:
    resp = await _get().chat.completions.create(
        model=model or config.LLM_BRIEF_MODEL,
        messages=_messages(system, prompt),
        temperature=temperature,
    )
    return resp.choices[0].message.content or ""


async def acomplete_with_tools(system: str, prompt: str, tools: list[dict],
                               model: str | None = None, temperature: float = 0.4) -> dict:
    """One completion that may pick a tool. Returns
    {"content": str, "tool_calls": [{"name", "arguments"(dict)}]}.
    Nothing is executed here — the caller proposes the call for human confirm."""
    import json

    resp = await _get().chat.completions.create(
        model=model or config.LLM_BRIEF_MODEL,
        messages=_messages(system, prompt),
        tools=tools,
        tool_choice="auto",
        temperature=temperature,
    )
    msg = resp.choices[0].message
    calls = []
    for tc in (msg.tool_calls or []):
        try:
            args = json.loads(tc.function.arguments or "{}")
        except json.JSONDecodeError:
            args = {}
        calls.append({"name": tc.function.name, "arguments": args})
    return {"content": msg.content or "", "tool_calls": calls}


async def astream(system: str, prompt: str, model: str | None = None, temperature: float = 0.4):
    """Yield text deltas as they arrive (for the low-latency live responder)."""
    stream = await _get().chat.completions.create(
        model=model or config.LLM_RESPONDER_MODEL,
        messages=_messages(system, prompt),
        temperature=temperature,
        stream=True,
    )
    async for chunk in stream:
        if chunk.choices:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta
