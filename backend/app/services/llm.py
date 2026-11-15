"""Provider-neutral, streaming LLM access for the lab agent.

Claude is the default (Claude Opus 5.5); OpenAI models (or anything behind an
OpenAI-compatible endpoint such as a LiteLLM proxy) are an option. Tools are
defined once in Anthropic's shape ({name, description, input_schema}) and
translated for OpenAI here; each conversation keeps its provider-native
message history so callers never handle tool_use / tool_calls blocks.

Configured from env (read at import; recreate the backend to change):
    PLABS_AI_ENABLED            1 to turn the AI features on
    PLABS_AI_API_KEY            Anthropic key (falls back to ANTHROPIC_API_KEY)
    PLABS_AI_MODEL              default model (claude-sonnet-5-5)
    PLABS_AI_CLAUDE_MODELS      Claude models offered in the UI (comma list)
    PLABS_AI_OPENAI_API_KEY     OpenAI key (falls back to OPENAI_API_KEY)
    PLABS_AI_OPENAI_BASE_URL    optional OpenAI-compatible endpoint (e.g. LiteLLM)
    PLABS_AI_OPENAI_MODELS      OpenAI models offered in the UI (comma list)
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Callable

logger = logging.getLogger(__name__)

_env = lambda k: os.environ.get(k, "").strip()          # noqa: E731
_list = lambda k: [m.strip() for m in _env(k).split(",") if m.strip()]  # noqa: E731

ANTHROPIC_KEY = _env("PLABS_AI_API_KEY") or _env("ANTHROPIC_API_KEY")
OPENAI_KEY = _env("PLABS_AI_OPENAI_API_KEY") or _env("OPENAI_API_KEY")
OPENAI_BASE_URL = _env("PLABS_AI_OPENAI_BASE_URL")
DEFAULT_MODEL = _env("PLABS_AI_MODEL") or "claude-sonnet-5-5"
CLAUDE_MODELS = _list("PLABS_AI_CLAUDE_MODELS") or ["claude-sonnet-5-5", "claude-opus-5-5"]
OPENAI_MODELS = _list("PLABS_AI_OPENAI_MODELS")

_LABELS = {"claude-opus-5-5": "Claude Opus 5.5", "claude-sonnet-5-5": "Claude Sonnet 5.5",
           "claude-haiku-5-5": "Claude Haiku 5.5", "claude-fable-5-2": "Claude Fable 5.2"}
# Claude models whose refusals can be re-run server-side on a fallback model.
_FALLBACK_OK = {"claude-opus-5-5", "claude-sonnet-5-5", "claude-fable-5-2", "claude-fable-5-1", "claude-opus-5"}


def provider_of(model: str) -> str:
    return "anthropic" if model.startswith("claude") else "openai"


def available_models() -> list[dict]:
    """Models the UI may offer: only providers with a key (or an endpoint)."""
    out = []
    if ANTHROPIC_KEY:
        out += [{"id": m, "label": _LABELS.get(m, m), "provider": "anthropic"} for m in CLAUDE_MODELS]
    if OPENAI_KEY or OPENAI_BASE_URL:
        out += [{"id": m, "label": m, "provider": "openai"} for m in OPENAI_MODELS]
    return out


def config_error(model: str | None = None) -> str | None:
    """Why the AI can't run (None when it can)."""
    model = model or DEFAULT_MODEL
    if provider_of(model) == "anthropic":
        if not ANTHROPIC_KEY:
            return "No Anthropic API key. Set PLABS_AI_API_KEY in .env and recreate the backend."
    elif not (OPENAI_KEY or OPENAI_BASE_URL):
        return "No OpenAI API key. Set PLABS_AI_OPENAI_API_KEY in .env and recreate the backend."
    return None


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict | None          # None: arguments weren't a JSON object (don't run it)


@dataclass
class Turn:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop: str = ""               # end | tool_use | pause | refusal | max_tokens
    note: str = ""               # user-facing notice (refusal, truncation)


# Callbacks the agent passes in: on_text(delta) and on_status(message).
TextCb = Callable[[str], None]


class AgentConversation:
    """One multi-turn agent conversation on one model. `step()` runs a model
    turn (streaming text through on_text) and records it; when it returns
    tool calls the caller runs them and passes the results to
    `add_tool_results()` before the next `step()`."""

    def __init__(self, model: str, tools: list[dict], server_tools: list[dict] | None = None):
        self.model = model
        self.provider = provider_of(model)
        self.tools = tools
        self.messages: list = []
        if self.provider == "anthropic":
            import anthropic
            self._client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
            # Client tools stream their input as it's generated; the agent
            # validates every input before running it.
            self._tools = [{**t, "eager_input_streaming": True} for t in tools] + list(server_tools or [])
        else:
            import openai
            self._client = openai.OpenAI(api_key=OPENAI_KEY or "not-needed",
                                         **({"base_url": OPENAI_BASE_URL} if OPENAI_BASE_URL else {}))
            self._tools = [{"type": "function", "function": {
                "name": t["name"], "description": t.get("description", ""),
                "parameters": t.get("input_schema") or {"type": "object", "properties": {}}}} for t in tools]

    def add_user(self, text: str) -> None:
        self._answer_interrupted()
        self.messages.append({"role": "user", "content": text})

    def _answer_interrupted(self) -> None:
        """A run stopped between a tool call and its result leaves the call
        unanswered, which the APIs reject: answer it before continuing."""
        if not self.messages or self.messages[-1].get("role") != "assistant":
            return
        last = self.messages[-1]
        if self.provider == "anthropic":
            content = last.get("content") or []
            ids = [b.get("id") if isinstance(b, dict) else getattr(b, "id", None) for b in content
                   if (b.get("type") if isinstance(b, dict) else getattr(b, "type", None)) == "tool_use"]
        else:
            ids = [c["id"] for c in last.get("tool_calls") or []]
        if ids:
            self.add_tool_results([(i, "Interrupted before this ran.", True) for i in ids if i])

    def add_tool_results(self, results: list[tuple[str, str, bool]]) -> None:
        """results: [(tool_call_id, text, is_error)] for every call of the last turn."""
        if self.provider == "anthropic":
            self.messages.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": cid, "content": text, **({"is_error": True} if err else {})}
                for cid, text, err in results]})
        else:
            self.messages.extend({"role": "tool", "tool_call_id": cid, "content": text} for cid, text, _ in results)

    def step(self, system: str, on_text: TextCb, on_status: TextCb) -> Turn:
        if self.provider == "anthropic":
            return self._step_anthropic(system, on_text, on_status)
        return self._step_openai(system, on_text)

    # -- Claude -----------------------------------------------------------------
    def _step_anthropic(self, system: str, on_text: TextCb, on_status: TextCb) -> Turn:
        kwargs: dict = {}
        if self.model in _FALLBACK_OK:
            # On a safety-classifier refusal the API re-runs the request on a
            # fallback model inside the same call (routing by refusal category).
            kwargs.update(betas=["server-side-fallback-2026-07-01"], fallbacks="default")
        with self._client.beta.messages.stream(
            model=self.model,
            max_tokens=64000,
            system=[{"type": "text", "text": system}],
            messages=self.messages,
            tools=self._tools,
            output_config={"effort": "high"},     # set explicitly (Opus 5.5 defaults to medium); config work wants high
            cache_control={"type": "ephemeral"},  # stable system + tools + growing history
            **kwargs,
        ) as stream:
            for event in stream:
                if event.type == "text":
                    on_text(event.text)
                elif event.type == "content_block_start" and getattr(event.content_block, "type", "") == "server_tool_use":
                    blk = event.content_block
                    on_status("Searching Cisco documentation" if blk.name == "web_search" else "Reading a Cisco document")
            msg = stream.get_final_message()

        stop = msg.stop_reason
        # Keep the assistant turn exactly as returned (thinking, server-tool and
        # fallback blocks must be passed back unchanged).
        self.messages.append({"role": "assistant", "content": msg.content})
        calls = [ToolCall(b.id, b.name, b.input if isinstance(b.input, dict) else None)
                 for b in msg.content if b.type == "tool_use"]
        if stop == "pause_turn":
            return Turn(stop="pause")
        if stop == "refusal":
            cat = getattr(getattr(msg, "stop_details", None), "category", None)
            self._close_dangling(calls)
            return Turn(stop="refusal", note=f"The model declined this request{f' ({cat})' if cat else ''}.")
        if stop == "max_tokens" and calls:
            # A tool call cut off by the token limit carries incomplete input.
            self._close_dangling(calls)
            return Turn(stop="max_tokens", note="The response was cut off before a tool call finished; nothing was run.")
        if stop == "max_tokens":
            return Turn(stop="max_tokens", note="The response hit the length limit and was cut off.")
        return Turn(tool_calls=calls, stop="tool_use" if calls else "end")

    def _close_dangling(self, calls: list[ToolCall]) -> None:
        """Answer tool calls we won't run, so the history stays valid."""
        if calls:
            self.add_tool_results([(c.id, "Not run.", True) for c in calls])

    # -- OpenAI-compatible --------------------------------------------------------
    def _step_openai(self, system: str, on_text: TextCb) -> Turn:
        stream = self._client.chat.completions.create(
            model=self.model, stream=True, max_completion_tokens=16000,
            messages=[{"role": "system", "content": system}, *self.messages],
            tools=self._tools, tool_choice="auto",
        )
        text, finish, calls = [], None, {}
        for chunk in stream:
            if not chunk.choices:
                continue
            ch = chunk.choices[0]
            d = ch.delta
            if d and d.content:
                text.append(d.content)
                on_text(d.content)
            for tc in (d.tool_calls or []) if d else []:
                acc = calls.setdefault(tc.index, {"id": "", "name": "", "args": ""})
                acc["id"] = tc.id or acc["id"]
                if tc.function:
                    acc["name"] = tc.function.name or acc["name"]
                    acc["args"] += tc.function.arguments or ""
            finish = ch.finish_reason or finish
        raw = [calls[i] for i in sorted(calls)]
        parsed = [ToolCall(c["id"], c["name"], _parse_args(c["args"])) for c in raw]
        self.messages.append({
            # content may only be null alongside tool_calls.
            "role": "assistant", "content": "".join(text) or (None if raw else ""),
            **({"tool_calls": [{"id": c["id"], "type": "function",
                                "function": {"name": c["name"], "arguments": c["args"] or "{}"}} for c in raw]}
               if raw else {}),
        })
        if parsed and finish == "length":
            self.add_tool_results([(c.id, "Not run.", True) for c in parsed])
            return Turn(stop="max_tokens", note="The response was cut off before a tool call finished; nothing was run.")
        return Turn(tool_calls=parsed, stop="tool_use" if parsed else "end")


def _parse_args(raw: str | None) -> dict | None:
    if not raw:
        return {}
    try:
        v = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Model returned non-JSON tool arguments: %r", raw[:200])
        return None
    return v if isinstance(v, dict) else None
