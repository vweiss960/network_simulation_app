"""Lab agent: tools against a real (test) DB, the loop with a scripted model."""
import pytest

from app.models.agent_action import AgentAction
from app.models.topology import Topology
from app.services import lab_agent, llm


@pytest.fixture
def lab(db, monkeypatch):
    from app.models.node_template import NodeTemplate
    from app.services import lab_service
    monkeypatch.setattr(lab_service, "request_sync", lambda db_, tid: False)
    monkeypatch.setattr(lab_service, "local_docker_images", lambda: {"xrd:1", "frr:1"})
    db.add(NodeTemplate(name="Cisco XRd Control Plane", kind="cisco_xrd", image_ref="xrd:1", interface_count=4))
    db.add(NodeTemplate(name="FRR Router", kind="linux", image_ref="frr:1", interface_count=4))
    db.add(NodeTemplate(name="Cisco Catalyst 9000v", kind="cisco_cat9kv", image_ref="", interface_count=8))
    t = Topology(name="agent-lab")
    db.add(t)
    db.commit()
    return t


def call(db, tid, name, args):
    return lab_agent._IMPL[name](db, tid, args, lambda e: None)


def test_topology_tools(db, lab):
    out = call(db, lab.id, "add_devices", {"devices": [
        {"type": "Cisco XRd Control Plane", "name": "R3", "x": 100, "y": 100},
        {"type": "xrd", "name": "R4", "x": 320, "y": 100},
        {"type": "FRR Router", "name": "F1", "x": 540, "y": 100}]})
    assert "R3" in out and "GigabitEthernet0/0/0/0" in out
    with pytest.raises(lab_agent.ToolError, match="no image"):
        call(db, lab.id, "add_devices", {"devices": [{"type": "Cisco Catalyst 9000v"}]})
    out = call(db, lab.id, "connect_devices", {"links": [
        {"a": "R4", "b": "R3", "a_interface": "GigabitEthernet0/0/0/1"}, {"a": "r4", "b": "F1"}]})
    assert "R4 GigabitEthernet0/0/0/1 <-> R3 GigabitEthernet0/0/0/0" in out
    state = call(db, lab.id, "get_lab_state", {})
    assert "R4: Cisco XRd (iosxr)" in state
    assert "GigabitEthernet0/0/0/1 -> R3 GigabitEthernet0/0/0/0" in state
    assert "free:" in state
    call(db, lab.id, "add_annotation", {"kind": "group", "text": "Area 0", "around": ["R3", "R4"], "color": "blue"})
    db.refresh(lab)
    g = lab.annotations[0]
    assert g["type"] == "group" and g["x"] < 100 and g["x"] + g["w"] > 320 + 56
    assert "Removed 1 cable" in call(db, lab.id, "disconnect", {"a": "R3", "b": "R4"})
    assert "Removed F1" in call(db, lab.id, "remove_device", {"device": "F1"})
    with pytest.raises(lab_agent.ToolError, match="No device named"):
        call(db, lab.id, "get_running_config", {"device": "R9"})
    assert "Cisco Catalyst 9000v" in call(db, lab.id, "list_device_types", {})


def test_lab_state_reads_runtime_by_node_id(db, lab, monkeypatch):
    from app.services import lab_service
    call(db, lab.id, "add_devices", {"devices": [{"type": "xrd", "name": "R1"}]})
    nid = db.query(lab_agent.NodeInstance).filter_by(name="R1").one().id
    monkeypatch.setattr(lab_service, "get_deployment_state", lambda db_, tid: {
        "status": "running", "nodes": {nid: {"ready_state": "up", "mgmt_ip": "10.1.1.5"}}})
    state = call(db, lab.id, "get_lab_state", {})
    assert "(1/1 devices up)" in state and "state up, mgmt 10.1.1.5" in state


def test_validation():
    s = lab_agent.TOOLS[[t["name"] for t in lab_agent.TOOLS].index("run_show")]["input_schema"]
    assert lab_agent._validate(s, {"device": "R1", "commands": ["show ver"]}) is None
    assert "required" in lab_agent._validate(s, {"device": "R1"})
    assert "must be a list" in lab_agent._validate(s, {"device": "R1", "commands": "show ver"})
    assert "not a known field" in lab_agent._validate(s, {"device": "R1", "commands": ["show ver"], "x": 1})
    assert "at least 1" in lab_agent._validate(s, {"device": "R1", "commands": []})
    w = lab_agent.TOOLS[[t["name"] for t in lab_agent.TOOLS].index("wait_for_devices")]["input_schema"]
    assert "<= 1200" in lab_agent._validate(w, {"timeout_s": 99999})


class FakeConvo:
    """Scripted model: a tool call, then a final answer."""
    def __init__(self, turns):
        self.turns, self.results = list(turns), []

    def add_user(self, text):
        self.user = text

    def step(self, system, on_text, on_status):
        t = self.turns.pop(0)
        if t.text:
            on_text(t.text)
        return t

    def add_tool_results(self, results):
        self.results.append(results)


def test_agent_loop_runs_tools_and_audits(db, lab, monkeypatch):
    monkeypatch.setattr(lab_agent, "SessionLocal", lambda: db)
    monkeypatch.setattr(db, "close", lambda: None)
    convo = FakeConvo([
        llm.Turn(tool_calls=[llm.ToolCall("c1", "add_devices", {"devices": [{"type": "xrd", "name": "R1"}]}),
                             llm.ToolCall("c2", "get_running_config", {"device": "nope"}),
                             llm.ToolCall("c3", "run_show", {"device": "R1"})], stop="tool_use"),
        llm.Turn(text="Done. Would you like me to apply this to R1?", stop="end"),
    ])
    sess = lab_agent.Session_(id="s1", topology_id=lab.id, model="claude-opus-5-5", convo=convo)
    events = []
    lab_agent.run(sess, "add a router", "admin", events.append)
    (results,) = convo.results
    assert results[0][2] is False and "Added: R1" in results[0][1]
    assert results[1][2] is True and "No device named" in results[1][1]
    assert results[2][2] is True and "commands is required" in results[2][1]
    kinds = [e["type"] for e in events]
    assert kinds.count("tool") == 6 and "lab_changed" in kinds and kinds[-1] == "text"
    acts = db.query(AgentAction).all()
    assert [a.tool for a in acts] == ["add_devices"] and acts[0].ok and acts[0].username == "admin"


def test_status_without_key(client, monkeypatch):
    monkeypatch.setattr(llm, "ANTHROPIC_KEY", "")
    monkeypatch.setattr(llm, "OPENAI_KEY", "")
    r = client.get("/api/ai/status").json()
    assert r["enabled"] is False and r["models"] == [] and r["error"]


def test_status_lists_models(client, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "AI_ENABLED", True)
    monkeypatch.setattr(llm, "ANTHROPIC_KEY", "sk-test")
    monkeypatch.setattr(llm, "OPENAI_KEY", "sk-test")
    monkeypatch.setattr(llm, "OPENAI_MODELS", ["gpt-x"])
    r = client.get("/api/ai/status").json()
    assert r["enabled"] and r["default_model"] == "claude-opus-5-5"
    assert {m["id"] for m in r["models"]} == {"claude-opus-5-5", "claude-sonnet-5-5", "gpt-x"}


def test_conversation_saved_and_restored(db, lab, monkeypatch):
    monkeypatch.setattr(lab_agent, "SessionLocal", lambda: db)
    monkeypatch.setattr(db, "close", lambda: None)
    monkeypatch.setattr(llm, "ANTHROPIC_KEY", "sk-test")
    sess = lab_agent.get_session(None, lab.id, "claude-opus-5-5")
    convo = FakeConvo([llm.Turn(tool_calls=[llm.ToolCall("c1", "get_lab_state", {})], stop="tool_use"),
                       llm.Turn(text="All good.", stop="end")])
    convo.messages = [{"role": "user", "content": "check"}, {"role": "assistant", "content": "ok"}]
    sess.convo = convo
    lab_agent.run_and_save(sess, "  Check the   lab please ", "admin", lambda e: None)
    rec = db.get(lab_agent.AgentConversation, sess.id)
    assert rec.title == "Check the lab please" and rec.username == "admin"
    lab_agent.drop_session(sess.id)                       # gone from memory
    again = lab_agent.get_session(sess.id, lab.id, "claude-opus-5-5")
    assert again.id == sess.id and again.convo.messages[1]["content"] == "ok"
    user, answer = again.ui
    assert user["parts"][0]["text"].strip() == "Check the   lab please"
    assert [p["kind"] for p in answer["parts"]] == ["tool", "text"] and answer["parts"][0]["state"] == "done"
    other = lab_agent.get_session(sess.id, lab.id, "claude-sonnet-5-5")   # another model: new conversation
    assert other.id != sess.id


def test_stop_skips_remaining_tools(db, lab, monkeypatch):
    monkeypatch.setattr(lab_agent, "SessionLocal", lambda: db)
    monkeypatch.setattr(db, "close", lambda: None)
    sess = lab_agent.Session_(id="s2", topology_id=lab.id, model="claude-opus-5-5", convo=None)

    def stop_after_first(*a, **k):
        sess.cancel.set()
        return True, "ok"
    monkeypatch.setattr(lab_agent, "_run_tool", stop_after_first)
    sess.convo = FakeConvo([llm.Turn(tool_calls=[llm.ToolCall("a", "get_lab_state", {}),
                                                 llm.ToolCall("b", "get_lab_state", {})], stop="tool_use")])
    events = []
    lab_agent.run(sess, "go", "admin", events.append)
    (results,) = sess.convo.results
    assert results[1] == ("b", "Not run: the user stopped the assistant.", True)
    assert "Stopped" in events[-1]["delta"]


def test_interrupted_tool_calls_are_answered(monkeypatch):
    c = llm.AgentConversation.__new__(llm.AgentConversation)
    c.provider, c.messages = "anthropic", [
        {"role": "user", "content": "x"},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "ping", "input": {}}]}]
    c.add_user("next")
    assert c.messages[2]["content"][0]["tool_use_id"] == "t1" and c.messages[3] == {"role": "user", "content": "next"}
