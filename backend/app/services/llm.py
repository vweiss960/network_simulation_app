"""Provider-neutral LLM access for the AI assistants (chat, config_ai, service_ai).

The routes keep their own agent loops and tool executors; this module only
owns the wire format. Tools are defined once in Anthropic's shape
({name, description, input_schema}) and translated per provider here, and the
provider-native message history lives inside a `Conversation` so the routes
never touch tool_use / tool_calls blocks directly.

Configured from env (read at import; recreate the backend container to change):
    PLABS_AI_PROVIDER   anthropic (default) | openai | litellm
    PLABS_AI_API_KEY    provider key (anthropic falls back to ANTHROPIC_API_KEY;
                          optional for a LiteLLM proxy without a master key)
    PLABS_AI_BASE_URL   API base URL — required for litellm (e.g. http://localhost:4000)
    PLABS_AI_MODEL      main model; required for openai / litellm (a LiteLLM model alias)
    PLABS_AI_TOOL_MODEL optional fast model for chat's tool-selection turns
                          (defaults to Haiku on anthropic, else PLABS_AI_MODEL)

openai and litellm share the OpenAI-compatible adapter — a LiteLLM proxy speaks
the OpenAI Chat Completions API and routes to whatever backend it's configured for.
"""
import json
import logging
import os
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

PROVIDER = os.environ.get("PLABS_AI_PROVIDER", "anthropic").strip().lower() or "anthropic"
BASE_URL = os.environ.get("PLABS_AI_BASE_URL", "").strip()
API_KEY = os.environ.get("PLABS_AI_API_KEY", "").strip() or (
    os.environ.get("ANTHROPIC_API_KEY", "").strip() if PROVIDER == "anthropic" else ""
)

_DEFAULT_MODELS = {
    "anthropic": ("claude-sonnet-4-6", "claude-haiku-4-5-20251001"),
}
_default_model, _default_tool_model = _DEFAULT_MODELS.get(PROVIDER, ("", ""))
MODEL = os.environ.get("PLABS_AI_MODEL", "").strip() or _default_model
TOOL_MODEL = os.environ.get("PLABS_AI_TOOL_MODEL", "").strip() or _default_tool_model or MODEL


def config_error() -> str | None:
    """Human-readable reason the AI can't run, or None when configured."""
    if PROVIDER not in ("anthropic", "openai", "litellm"):
        return f"Unknown PLABS_AI_PROVIDER '{PROVIDER}' (expected anthropic, openai or litellm)."
    if PROVIDER == "litellm" and not BASE_URL:
        return "PLABS_AI_PROVIDER=litellm requires PLABS_AI_BASE_URL (the LiteLLM proxy URL)."
    if PROVIDER in ("anthropic", "openai") and not API_KEY:
        hint = "PLABS_AI_API_KEY (or ANTHROPIC_API_KEY)" if PROVIDER == "anthropic" else "PLABS_AI_API_KEY"
        return f"No API key configured for {PROVIDER}. Set {hint} on the backend and recreate it."
    if not MODEL:
        return f"PLABS_AI_MODEL must be set when PLABS_AI_PROVIDER={PROVIDER}."
    return None


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict


@dataclass
class Turn:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)


def to_openai_tools(tools: list[dict]) -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("input_schema") or {"type": "object", "properties": {}},
            },
        }
        for t in tools
    ]


_DROPPED_CALLS_NOTE = ("\n\n(The model's tool call was cut off or malformed, so it was not run. "
                       "Try again, or ask for a smaller change.)")


def _parse_args(raw: str | None) -> dict | None:
    """Tool-call arguments as a dict; None when they aren't a JSON object
    (e.g. cut off by the token limit). Callers must not run such a call —
    executing it with {} would, for instance, blank a template body."""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("LLM returned non-JSON tool arguments: %r", raw[:200])
        return None
    return parsed if isinstance(parsed, dict) else None


class Conversation:
    """One agent conversation. `step()` runs a model turn and records it in the
    history; when it returns tool calls, the caller executes them and passes the
    results to `add_tool_results()` before the next `step()`."""

    def __init__(self, messages: list[dict], tools: list[dict]):
        self.tools = tools
        self.messages = [{"role": m["role"], "content": m["content"]} for m in messages]
        if PROVIDER == "anthropic":
            import anthropic
            self._client = anthropic.Anthropic(api_key=API_KEY, **({"base_url": BASE_URL} if BASE_URL else {}))
        else:
            import openai
            self._client = openai.OpenAI(
                # The openai SDK insists on a key; a keyless LiteLLM proxy ignores it.
                api_key=API_KEY or "not-needed",
                **({"base_url": BASE_URL} if BASE_URL else {}),
            )
            self._openai_tools = to_openai_tools(tools)

    def step(self, system: str, *, model: str | None = None, max_tokens: int = 4096,
             allow_tools: bool = True) -> Turn:
        """Run one model turn. With allow_tools=False the model must answer in
        text (tool calls it makes anyway are dropped)."""
        model = model or MODEL
        if PROVIDER == "anthropic":
            return self._step_anthropic(system, model, max_tokens, allow_tools)
        return self._step_openai(system, model, max_tokens, allow_tools)

    def add_tool_results(self, results: list[tuple[str, str]]) -> None:
        """results: [(tool_call_id, result_text), ...] for the last turn's calls."""
        if PROVIDER == "anthropic":
            self.messages.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": cid, "content": text} for cid, text in results
            ]})
        else:
            self.messages.extend(
                {"role": "tool", "tool_call_id": cid, "content": text} for cid, text in results
            )

    def _step_anthropic(self, system: str, model: str, max_tokens: int, allow_tools: bool) -> Turn:
        kwargs = {"tools": self.tools} if allow_tools else {}
        response = self._client.messages.create(
            model=model, max_tokens=max_tokens, system=system, messages=self.messages, **kwargs,
        )
        turn = Turn(
            text="".join(b.text for b in response.content if getattr(b, "type", None) == "text"),
            tool_calls=[ToolCall(b.id, b.name, b.input or {}) for b in response.content
                        if getattr(b, "type", None) == "tool_use"] if allow_tools else [],
        )
        if turn.tool_calls and getattr(response, "stop_reason", None) == "max_tokens":
            # A tool_use cut off by the token limit carries incomplete input.
            logger.warning("Dropping tool calls from a max_tokens-truncated response")
            turn.tool_calls = []
            turn.text += _DROPPED_CALLS_NOTE
        if turn.tool_calls:
            self.messages.append({"role": "assistant", "content": response.content})
        return turn

    def _step_openai(self, system: str, model: str, max_tokens: int, allow_tools: bool) -> Turn:
        # Tools stay attached even when disallowed: providers behind LiteLLM
        # (e.g. Anthropic, Bedrock) reject a history containing tool calls
        # unless tools are declared. tool_choice="none" forces a text answer.
        response = self._client.chat.completions.create(
            model=model,
            max_completion_tokens=max_tokens,
            messages=[{"role": "system", "content": system}, *self.messages],
            tools=self._openai_tools,
            tool_choice="auto" if allow_tools else "none",
        )
        choice = response.choices[0]
        msg = choice.message
        raw_calls = (msg.tool_calls or []) if allow_tools else []
        calls = [ToolCall(c.id, c.function.name, _parse_args(c.function.arguments)) for c in raw_calls]
        turn = Turn(text=msg.content or "", tool_calls=calls)
        if calls and (getattr(choice, "finish_reason", None) == "length"
                      or any(c.input is None for c in calls)):
            # Truncated or malformed arguments: running the call with partial
            # or empty input is worse than not running it at all.
            logger.warning("Dropping truncated/malformed tool calls")
            raw_calls, turn.tool_calls = [], []
            turn.text += _DROPPED_CALLS_NOTE
        if turn.tool_calls:
            self.messages.append({
                "role": "assistant",
                "content": msg.content,
                "tool_calls": [
                    {"id": c.id, "type": "function",
                     "function": {"name": c.function.name, "arguments": c.function.arguments or "{}"}}
                    for c in raw_calls
                ],
            })
        return turn
