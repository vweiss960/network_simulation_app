"""Packet capture: which host port a link is captured on, and the tshark
process lifecycle (with a stand-in tshark script)."""
import os
import time

import pytest

from app.models.topology import Topology
from app.services import host_net, lab_agent
from app.services import packet_capture as pc


@pytest.fixture
def lab(db, monkeypatch):
    from app.models.node_template import NodeTemplate
    from app.services import lab_service
    monkeypatch.setattr(lab_service, "request_sync", lambda db_, tid: False)
    monkeypatch.setattr(lab_service, "local_docker_images", lambda: {"xrd:1"})
    db.add(NodeTemplate(name="Cisco XRd Control Plane", kind="cisco_xrd", image_ref="xrd:1", interface_count=4))
    t = Topology(name="cap-lab")
    db.add(t)
    db.commit()
    impl = lambda name, args: lab_agent._IMPL[name](db, t.id, args, lambda e: None)   # noqa: E731
    impl("add_devices", {"devices": [{"type": "xrd", "name": "R1"}, {"type": "xrd", "name": "R2"}]})
    impl("connect_devices", {"links": [{"a": "R1", "b": "R2", "a_interface": "GigabitEthernet0/0/0/2"}]})
    return t


def _link_id(db, tid):
    from app.models.link import Link
    return db.query(Link).filter_by(topology_id=tid).one().id


def _node_id(db, name):
    return db.query(lab_agent.NodeInstance).filter_by(name=name).one().id


def test_link_endpoint_is_source_port_host_half(db, lab, monkeypatch):
    monkeypatch.setattr(host_net, "link_exists", lambda dev: True)
    iface, title, side = pc.link_endpoint(db, lab.id, _link_id(db, lab.id))
    assert iface == host_net.port_name(lab.id, _node_id(db, "R1"), 2)
    assert title == "R1 GigabitEthernet0/0/0/2 ↔ R2 GigabitEthernet0/0/0/0"
    assert side == "R1 GigabitEthernet0/0/0/2"


def test_link_endpoint_needs_running_device(db, lab, monkeypatch):
    monkeypatch.setattr(host_net, "link_exists", lambda dev: False)
    with pytest.raises(pc.CaptureError, match="isn't running"):
        pc.link_endpoint(db, lab.id, _link_id(db, lab.id))
    with pytest.raises(pc.CaptureError, match="isn't part of the running lab"):
        pc.link_endpoint(db, lab.id, 99999)


FAKE_TSHARK = """#!/bin/sh
# Stand-in for tshark: write the -w file, print two rows, run until SIGINT.
out=""; prev=""
for a in "$@"; do [ "$prev" = "-w" ] && out="$a"; prev="$a"; done
echo pcap > "$out"
trap 'exit 0' INT
printf '1\\t0.000000000\\t10.0.0.1\\t10.0.0.2\\tICMP\\t98\\tEcho (ping) request\\n'
printf '2\\t0.000412000\\t10.0.0.2\\t10.0.0.1\\tICMP\\t98\\tEcho (ping) reply\\twith\\ttabs\\n'
while true; do sleep 0.05; done
"""


@pytest.fixture
def fake_tshark(tmp_path, monkeypatch):
    exe = tmp_path / "tshark"
    exe.write_text(FAKE_TSHARK)
    os.chmod(exe, 0o755)
    monkeypatch.setattr(pc.shutil, "which", lambda name: str(exe))
    monkeypatch.setattr(pc, "CAPTURE_DIR", tmp_path / "captures")
    monkeypatch.setattr(host_net, "link_exists", lambda dev: True)
    yield
    for c in list(pc._CAPS.values()):
        pc.delete(c)


def test_capture_lifecycle(db, lab, fake_tshark):
    cap = pc.start(db, lab.id, _link_id(db, lab.id), "alice", "icmp")
    for _ in range(40):
        if len(cap.rows) == 2:
            break
        time.sleep(0.05)
    assert cap.rows[0] == [1, 0.0, "10.0.0.1", "10.0.0.2", "ICMP", 98, "Echo (ping) request"]
    assert cap.rows[1][6] == "Echo (ping) reply\twith\ttabs"       # info keeps its tabs
    assert [c.id for c in pc.list_for(lab.id)] == [cap.id]
    s = cap.summary()
    assert s["state"] == "running" and s["packets"] == 2 and s["filter"] == "icmp"
    pc.stop(cap)
    assert cap.state == "stopped" and cap.path.exists()
    pc.delete(cap)
    assert not cap.path.exists() and pc.list_for(lab.id) == []


def test_capture_routes(client, db, lab, fake_tshark):
    lid = _link_id(db, lab.id)
    r = client.post(f"/api/topologies/{lab.id}/links/{lid}/capture", json={"filter": ""})
    assert r.status_code == 200, r.text
    cid = r.json()["id"]
    assert client.get(f"/api/topologies/{lab.id}/captures").json()[0]["id"] == cid
    assert client.post(f"/api/topologies/{lab.id}/captures/{cid}/stop").json()["state"] == "stopped"
    d = client.get(f"/api/topologies/{lab.id}/captures/{cid}/download")
    assert d.status_code == 200 and "R1_GigabitEthernet0_0_0_2" in d.headers["content-disposition"]
    assert client.get(f"/api/topologies/{lab.id + 1}/captures/{cid}").status_code == 404
    assert client.delete(f"/api/topologies/{lab.id}/captures/{cid}").json() == {"ok": True}


def test_stop_topology_stops_its_captures(db, lab, fake_tshark):
    cap = pc.start(db, lab.id, _link_id(db, lab.id), "alice")
    pc.stop_topology(lab.id)
    assert cap.state == "stopped"
