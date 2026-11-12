import asyncio

import pytest

from app.services import device_exec
from app.services.device_exec import ExecError, Target, _config_lines, normalize_config
from tests.factories import make_node, make_topology


def test_config_lines_drop_mode_and_commit_noise():
    cfg = "configure terminal\ninterface Lo0\n ipv4 address 1.1.1.1/32\n\ncommit\nend\n"
    assert _config_lines(cfg) == ["interface Lo0", " ipv4 address 1.1.1.1/32"]


def test_normalize_xr_running_config():
    raw = ("!! Building configuration...\nThu Nov 12 14:36:11.511 UTC\n!! IOS XR Configuration 25.4.2\n"
           "!! Last configuration change at Thu Nov 12 by clab\n!\nhostname R1\nend")
    out = normalize_config("iosxr", raw)
    assert "Building" not in out and "Last configuration change" not in out and "14:36" not in out
    assert "hostname R1" in out


def _t(**kw):
    base = dict(node_id=1, name="H1", kind="linux", family="linux", host="", username="", password="",
                container="c", image="plabs/host:latest")
    base.update(kw)
    return Target(**base)


def test_linux_cli_uses_docker_exec(monkeypatch):
    calls = []
    monkeypatch.setattr(device_exec, "_docker_exec", lambda t, argv, timeout: (calls.append(argv), (0, "ok\n"))[1])
    res = asyncio.run(device_exec.run_cli(_t(), ["ip -br addr"]))
    assert res == [{"command": "ip -br addr", "output": "ok"}] and calls == [["sh", "-c", "ip -br addr"]]
    calls.clear()
    asyncio.run(device_exec.run_cli(_t(image="plabs/frr:latest"), ["show ip route"]))
    assert calls == [["vtysh", "-c", "show ip route"]]


def test_plain_linux_has_no_config_push():
    with pytest.raises(ExecError, match="isn't supported"):
        asyncio.run(device_exec.configure(_t(), "hostname x"))


def test_frr_config_via_vtysh(monkeypatch):
    seen = {}
    monkeypatch.setattr(device_exec, "_docker_exec", lambda t, argv, timeout: (seen.setdefault("argv", argv), (0, ""))[1])
    r = asyncio.run(device_exec.configure(_t(image="plabs/frr:latest"), "router ospf\n network 10.0.0.0/8 area 0"))
    assert r["ok"] and seen["argv"][:3] == ["vtysh", "-c", "configure terminal"]
    assert "router ospf" in seen["argv"] and seen["argv"][-1] == "write memory"


def test_target_for_rejects_networks_and_undeployable(db):
    t = make_topology(db)
    net = make_node(db, t, kind="bridge", icon="cloud")
    noimg = make_node(db, t, kind="cisco_xrd", image="")
    with pytest.raises(ExecError, match="network"):
        device_exec.target_for(db, t.id, net.id)
    with pytest.raises(ExecError, match="deployable"):
        device_exec.target_for(db, t.id, noimg.id)


def test_snapshot_routes(client, db, monkeypatch):
    async def fake_running(t, timeout=60):
        return "hostname R1\n"
    monkeypatch.setattr(device_exec, "running_config", fake_running)
    monkeypatch.setattr(device_exec, "target_for",
                        lambda db, tid, nid: _t(node_id=nid, name="R1", kind="cisco_xrd", family="iosxr", host="10.0.0.1"))
    t = client.post("/api/topologies/", json={"name": "S"}).json()
    n = client.post(f"/api/topologies/{t['id']}/nodes/", json={"kind": "cisco_xrd", "image_ref": "x"}).json()
    s = client.post(f"/api/topologies/{t['id']}/nodes/{n['id']}/snapshots", json={"label": "base"}).json()
    assert s["label"] == "base" and s["family"] == "iosxr"
    full = client.get(f"/api/topologies/{t['id']}/snapshots/{s['id']}").json()
    assert full["config"] == "hostname R1\n"
    r = client.post(f"/api/topologies/{t['id']}/snapshots/{s['id']}/restore", json={"mode": "reboot"}).json()
    assert r["ok"] and r["mode"] == "reboot"
    assert client.get(f"/api/topologies/{t['id']}/nodes/{n['id']}").json()["startup_config"] == "hostname R1\n"
    assert client.post(f"/api/topologies/{t['id']}/snapshots/{s['id']}/restore", json={"mode": "bogus"}).status_code == 400
