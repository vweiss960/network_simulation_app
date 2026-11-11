"""Provider translation in app.services.llm — fake SDK clients, no network."""
from types import SimpleNamespace as NS

import pytest

from app.services import llm

TOOLS = [{
    "name": "add_node",
    "description": "Add a node",
    "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
}]


class FakeCreate:
    """Records each request and replays canned responses in order."""
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        # Snapshot messages — the Conversation mutates its list after the call.
        self.calls.append({**kwargs, "messages": list(kwargs["messages"])})
        return self.responses.pop(0)


def _conversation(monkeypatch, provider, responses):
    monkeypatch.setattr(llm, "PROVIDER", provider)
    monkeypatch.setattr(llm, "API_KEY", "test-key")
    monkeypatch.setattr(llm, "BASE_URL", "")
    monkeypatch.setattr(llm, "MODEL", "main-model")
    conv = llm.Conversation([{"role": "user", "content": "add r1"}], TOOLS)
    fake = FakeCreate(responses)
    conv._client = NS(messages=fake, chat=NS(completions=fake))
    return conv, fake


def _openai_resp(content=None, tool_calls=None):
    return NS(choices=[NS(message=NS(content=content, tool_calls=tool_calls))])


def _openai_call(cid, name, arguments):
    return NS(id=cid, function=NS(name=name, arguments=arguments))


def test_to_openai_tools():
    out = llm.to_openai_tools(TOOLS)
    assert out == [{
        "type": "function",
        "function": {"name": "add_node", "description": "Add a node", "parameters": TOOLS[0]["input_schema"]},
    }]


@pytest.mark.parametrize("provider", ["openai", "litellm"])
def test_openai_tool_round_trip(monkeypatch, provider):
    conv, fake = _conversation(monkeypatch, provider, [
        _openai_resp("Adding it.", [_openai_call("call_1", "add_node", '{"name": "r1"}')]),
        _openai_resp("Done."),
    ])

    turn = conv.step("SYS", max_tokens=100)
    assert turn.text == "Adding it."
    assert [(c.id, c.name, c.input) for c in turn.tool_calls] == [("call_1", "add_node", {"name": "r1"})]
    first = fake.calls[0]
    assert first["model"] == "main-model"
    assert first["max_completion_tokens"] == 100
    assert first["tool_choice"] == "auto"
    assert first["messages"][0] == {"role": "system", "content": "SYS"}
    assert first["tools"][0]["function"]["name"] == "add_node"

    conv.add_tool_results([("call_1", "Added node r1")])
    turn = conv.step("SYS")
    assert turn.text == "Done." and turn.tool_calls == []

    history = fake.calls[1]["messages"]
    assert history[2]["role"] == "assistant"
    assert history[2]["tool_calls"][0]["id"] == "call_1"
    assert history[2]["tool_calls"][0]["function"] == {"name": "add_node", "arguments": '{"name": "r1"}'}
    assert history[3] == {"role": "tool", "tool_call_id": "call_1", "content": "Added node r1"}


def test_openai_disallowed_tools_forces_text(monkeypatch):
    conv, fake = _conversation(monkeypatch, "litellm", [
        _openai_resp("Summary", [_openai_call("call_x", "add_node", "{}")]),
    ])
    turn = conv.step("SYS", model="other", allow_tools=False)
    assert turn.text == "Summary" and turn.tool_calls == []
    assert fake.calls[0]["tool_choice"] == "none"
    assert fake.calls[0]["model"] == "other"
    assert len(conv.messages) == 1  # nothing appended for a final answer


@pytest.mark.parametrize("arguments", ["{not json", "[1]", '{"body": "trunc'])
def test_openai_bad_arguments_are_not_run(monkeypatch, arguments):
    conv, _ = _conversation(monkeypatch, "openai", [
        _openai_resp(None, [_openai_call("c1", "add_node", '{"name": "r1"}'),
                            _openai_call("c2", "add_node", arguments)]),
    ])
    turn = conv.step("SYS")
    assert turn.tool_calls == []
    assert "not run" in turn.text
    assert len(conv.messages) == 1  # no dangling assistant tool_calls


def test_openai_length_truncated_calls_are_not_run(monkeypatch):
    resp = _openai_resp(None, [_openai_call("c1", "add_node", '{"name": "r1"}')])
    resp.choices[0].finish_reason = "length"
    conv, _ = _conversation(monkeypatch, "openai", [resp])
    assert conv.step("SYS").tool_calls == []


def test_anthropic_max_tokens_truncated_calls_are_not_run(monkeypatch):
    conv, _ = _conversation(monkeypatch, "anthropic", [NS(stop_reason="max_tokens", content=[
        NS(type="tool_use", id="tu_1", name="add_node", input={}),
    ])])
    turn = conv.step("SYS")
    assert turn.tool_calls == [] and "not run" in turn.text
    assert len(conv.messages) == 1


def test_anthropic_tool_round_trip(monkeypatch):
    content = [
        NS(type="text", text="Adding it."),
        NS(type="tool_use", id="tu_1", name="add_node", input={"name": "r1"}),
    ]
    conv, fake = _conversation(monkeypatch, "anthropic", [
        NS(content=content),
        NS(content=[NS(type="text", text="Done.")]),
    ])

    turn = conv.step("SYS")
    assert turn.text == "Adding it."
    assert [(c.id, c.name, c.input) for c in turn.tool_calls] == [("tu_1", "add_node", {"name": "r1"})]
    assert fake.calls[0]["system"] == "SYS" and fake.calls[0]["tools"] == TOOLS

    conv.add_tool_results([("tu_1", "Added node r1")])
    turn = conv.step("SYS", allow_tools=False)
    assert turn.text == "Done."
    assert "tools" not in fake.calls[1]
    history = fake.calls[1]["messages"]
    assert history[1] == {"role": "assistant", "content": content}
    assert history[2] == {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "tu_1", "content": "Added node r1"},
    ]}


@pytest.mark.parametrize("provider,key,base,model,expect", [
    ("anthropic", "k", "", "m", None),
    ("anthropic", "", "", "m", "No API key"),
    ("openai", "k", "", "", "PHANERO_AI_MODEL must be set"),
    ("openai", "k", "", "gpt", None),
    ("litellm", "", "", "alias", "requires PHANERO_AI_BASE_URL"),
    ("litellm", "", "http://localhost:4000", "alias", None),  # keyless proxy is fine
    ("bogus", "k", "", "m", "Unknown PHANERO_AI_PROVIDER"),
])
def test_config_error(monkeypatch, provider, key, base, model, expect):
    monkeypatch.setattr(llm, "PROVIDER", provider)
    monkeypatch.setattr(llm, "API_KEY", key)
    monkeypatch.setattr(llm, "BASE_URL", base)
    monkeypatch.setattr(llm, "MODEL", model)
    err = llm.config_error()
    if expect is None:
        assert err is None
    else:
        assert expect in err
