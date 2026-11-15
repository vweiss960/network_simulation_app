"""Provider layer: model availability, and one streamed turn per provider
against fake SDK clients (no network)."""
from types import SimpleNamespace as NS

from app.services import llm


def test_models_follow_keys(monkeypatch):
    monkeypatch.setattr(llm, "ANTHROPIC_KEY", "")
    monkeypatch.setattr(llm, "OPENAI_KEY", "")
    monkeypatch.setattr(llm, "OPENAI_BASE_URL", "")
    assert llm.available_models() == []
    assert "Anthropic" in llm.config_error("claude-opus-5-5")
    monkeypatch.setattr(llm, "ANTHROPIC_KEY", "k")
    monkeypatch.setattr(llm, "OPENAI_MODELS", ["gpt-x"])
    assert [m["id"] for m in llm.available_models()] == ["claude-sonnet-5-5", "claude-opus-5-5"]
    assert llm.config_error("claude-opus-5-5") is None and "OpenAI" in llm.config_error("gpt-x")
    monkeypatch.setattr(llm, "OPENAI_BASE_URL", "http://litellm:4000")
    assert "gpt-x" in [m["id"] for m in llm.available_models()]
    assert llm.provider_of("claude-sonnet-5-5") == "anthropic" and llm.provider_of("gpt-x") == "openai"


class FakeStream:
    def __init__(self, events, final):
        self.events, self.final = events, final
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False
    def __iter__(self):
        return iter(self.events)
    def get_final_message(self):
        return self.final


def _anthropic(monkeypatch, final, events=()):
    seen = {}
    def stream(**kw):
        seen.update(kw)
        return FakeStream(list(events), final)
    c = llm.AgentConversation.__new__(llm.AgentConversation)
    c.model, c.provider, c.messages = "claude-opus-5-5", "anthropic", []
    c._tools = [{"name": "t", "input_schema": {}}]
    c._client = NS(beta=NS(messages=NS(stream=stream)))
    return c, seen


def test_anthropic_turn(monkeypatch):
    final = NS(stop_reason="tool_use", content=[
        NS(type="text", text="Checking."), NS(type="tool_use", id="tu1", name="get_lab_state", input={})])
    c, seen = _anthropic(monkeypatch, final, [NS(type="text", text="Checking.")])
    c.add_user("hi")
    got = []
    t = c.step("SYS", got.append, got.append)
    assert got == ["Checking."] and t.stop == "tool_use" and t.tool_calls[0].name == "get_lab_state"
    assert seen["model"] == "claude-opus-5-5" and seen["fallbacks"] == "default"
    assert seen["betas"] == ["server-side-fallback-2026-07-01"] and seen["output_config"] == {"effort": "high"}
    assert "thinking" not in seen and "temperature" not in seen
    assert c.messages[-1]["content"] is final.content     # assistant turn kept verbatim
    c.add_tool_results([("tu1", "ok", False)])
    assert c.messages[-1]["content"][0]["tool_use_id"] == "tu1"


def test_anthropic_refusal_closes_calls(monkeypatch):
    final = NS(stop_reason="refusal", stop_details=NS(category="cyber"),
               content=[NS(type="tool_use", id="tu1", name="apply_config", input={"device": "R1"})])
    c, _ = _anthropic(monkeypatch, final)
    c.add_user("x")
    t = c.step("SYS", lambda d: None, lambda d: None)
    assert t.stop == "refusal" and "cyber" in t.note and not t.tool_calls
    assert c.messages[-1]["content"][0]["is_error"] is True   # dangling tool_use answered


def test_openai_turn_accumulates_tool_calls():
    def chunk(content=None, calls=None, finish=None):
        return NS(choices=[NS(delta=NS(content=content, tool_calls=calls), finish_reason=finish)])
    tc = lambda i, id_=None, name=None, args="": NS(index=i, id=id_, function=NS(name=name, arguments=args))  # noqa: E731
    chunks = [chunk("Look"), chunk("ing."), chunk(calls=[tc(0, "c1", "run_show", '{"device": "R1", ')]),
              chunk(calls=[tc(0, args='"commands": ["show ver"]}')]), chunk(finish="tool_calls")]
    c = llm.AgentConversation.__new__(llm.AgentConversation)
    c.model, c.provider, c.messages, c._tools = "gpt-x", "openai", [], []
    c._client = NS(chat=NS(completions=NS(create=lambda **kw: iter(chunks))))
    got = []
    t = c.step("SYS", got.append, got.append)
    assert "".join(got) == "Looking." and t.tool_calls[0].input == {"device": "R1", "commands": ["show ver"]}
    assert c.messages[-1]["tool_calls"][0]["function"]["name"] == "run_show"
