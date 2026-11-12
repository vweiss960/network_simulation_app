"""Network nodes, bridged mgmt network, IPAM — host commands never run."""
import pytest
import yaml

from app.models.node_instance import NodeInstance
from app.services import host_net, lab_network
from app.services.clab_yaml import generate_clab_yaml
from app.services.host_net import HostNetError, Segment
from app.services.topology_ops import TopologyError, set_network
from tests.factories import make_link, make_node, make_topology

NICS = [
    {"name": "eno1", "protected": True, "carrier": True},
    {"name": "eno2", "protected": False, "carrier": False},
    {"name": "enp66s0f0", "protected": False, "carrier": True},
]


@pytest.fixture(autouse=True)
def fake_nics(monkeypatch):
    monkeypatch.setattr(host_net, "list_nics", lambda: NICS)


def test_bridge_names_fit_kernel_limit():
    assert host_net.segment_bridge_name("eno2", None) == "pl-eno2"
    assert host_net.segment_bridge_name("eno2", 120) == "pl-eno2-120"
    long = host_net.segment_bridge_name("enp66s0f0", 4094)
    assert len(long) <= 15 and long == host_net.segment_bridge_name("enp66s0f0", 4094)
    assert host_net.vlan_ifname("enp66s0f0", 4094) == "enp66s0f0.4094"
    assert len(host_net.bridge_port_name(123456, 987654, 99)) <= 15


def test_access_on_servers_uplink_is_refused_but_trunk_ok():
    with pytest.raises(HostNetError, match="trunk mode"):
        host_net.validate_segment(Segment("access", "eno1"), NICS)
    host_net.validate_segment(Segment("trunk", "eno1", 300), NICS)
    with pytest.raises(HostNetError, match="VLAN ID"):
        host_net.validate_segment(Segment("trunk", "eno2", 0), NICS)
    with pytest.raises(HostNetError, match="not a physical NIC"):
        host_net.validate_segment(Segment("access", "wlan9"), NICS)


def _bridged(**kw):
    base = dict(mode="bridged", nic="eno2", vlan=0, subnet="10.50.0.0/24", gateway="10.50.0.1",
                host_ip="10.50.0.10", pool_start="10.50.0.100", pool_end="10.50.0.102")
    base.update(kw)
    return lab_network.MgmtSettings(**base)


def test_mgmt_validation():
    s = lab_network.validate(_bridged())
    assert s.host_ip == "10.50.0.10/24" and s.bridge == "pl-eno2"
    with pytest.raises(HostNetError, match="inside the device pool"):
        lab_network.validate(_bridged(gateway="10.50.0.101"))
    with pytest.raises(HostNetError, match="not a usable address"):
        lab_network.validate(_bridged(pool_end="10.60.0.5"))
    with pytest.raises(HostNetError, match="Choose the NIC"):
        lab_network.validate(_bridged(nic=""))
    assert lab_network.validate(lab_network.MgmtSettings()).mode == "internal"


def test_ipam_unique_sticky_and_exhaustion(db):
    s = lab_network.save_settings(db, lab_network.validate(_bridged()))
    t1, t2 = make_topology(db, "A"), make_topology(db, "B")
    a, b = make_node(db, t1), make_node(db, t1)
    c = make_node(db, t2)
    ips = lab_network.assign_ips(db, s, [a.id, b.id])
    assert sorted(ips.values()) == ["10.50.0.100", "10.50.0.101"]
    assert lab_network.assign_ips(db, s, [a.id]) == {a.id: ips[a.id]}       # sticky
    assert lab_network.assign_ips(db, s, [c.id]) == {c.id: "10.50.0.102"}  # unique across labs
    d = make_node(db, t2)
    with pytest.raises(HostNetError, match="exhausted"):
        lab_network.assign_ips(db, s, [d.id])


def test_yaml_bridged_mgmt_and_network_nodes(db):
    lab_network.save_settings(db, lab_network.validate(_bridged()))
    t = make_topology(db)
    r1 = make_node(db, t, name="R1", kind="cisco_xrd", image="xrd")
    r2 = make_node(db, t, name="R2", kind="cisco_xrd", image="xrd")
    wan = make_node(db, t, name="WAN", kind="bridge", icon="cloud")
    set_network(db, wan, {"mode": "trunk", "nic": "eno2", "vlan": 120})
    sw = make_node(db, t, name="LAN", kind="bridge", icon="switch")
    make_link(db, t, r1, wan)
    make_link(db, t, r2, wan)
    make_link(db, t, r1, sw)
    db.refresh(wan)
    assert [i["name"] for i in wan.interfaces] == ["port1", "port2"]   # ports grow on demand

    spec = generate_clab_yaml(db, t.id)
    doc = yaml.safe_load(spec["yaml"])
    assert doc["mgmt"] == {"network": "plabs-mgmt-bridged", "ipv4-subnet": "10.50.0.0/24", "ipv4-gw": "10.50.0.1"}
    nodes = doc["topology"]["nodes"]
    assert nodes["pl-eno2-120"] == {"kind": "bridge"}
    sw_bridge = host_net.internal_bridge_name(t.id, sw.id)
    assert nodes[sw_bridge] == {"kind": "bridge"}
    assert nodes["R1"]["mgmt-ipv4"] == "10.50.0.100" and nodes["R2"]["mgmt-ipv4"] == "10.50.0.101"
    eps = [l["endpoints"] for l in doc["topology"]["links"]]
    p1 = host_net.bridge_port_name(t.id, wan.id, 1)
    assert ["R1:Gi0-0-0-0", f"pl-eno2-120:{p1}"] in eps
    assert {b["bridge"] for b in spec["resource_estimate"]["bridges"]} == {"pl-eno2-120", sw_bridge}


def test_internal_mode_has_no_static_ips(db):
    t = make_topology(db)
    make_node(db, t, name="A")
    doc = yaml.safe_load(generate_clab_yaml(db, t.id)["yaml"])
    assert doc["mgmt"]["network"] == "plabs-mgmt"
    assert "mgmt-ipv4" not in doc["topology"]["nodes"]["A"]


def test_unconfigured_network_node_is_skipped(db):
    t = make_topology(db)
    make_node(db, t, name="NET1", kind="bridge", icon="cloud")
    spec = generate_clab_yaml(db, t.id)
    assert spec["skipped_nodes"][0]["name"] == "NET1"


def test_network_rules(db):
    t = make_topology(db)
    n1 = make_node(db, t, kind="bridge", icon="cloud")
    n2 = make_node(db, t, kind="bridge", icon="cloud")
    set_network(db, n1, {"mode": "trunk", "nic": "eno2", "vlan": 10})
    with pytest.raises(TopologyError, match="already connects"):
        set_network(db, n2, {"mode": "trunk", "nic": "eno2", "vlan": 10})
    with pytest.raises(TopologyError, match="trunk mode"):
        set_network(db, n2, {"mode": "access", "nic": "eno1"})
    with pytest.raises(TopologyError, match="can't be cabled directly"):
        make_link(db, t, n1, n2)
    r = make_node(db, t, kind="linux", image="x")
    with pytest.raises(TopologyError):
        set_network(db, r, {"mode": "internal"})


def test_network_api_patch(client, db):
    t = client.post("/api/topologies/", json={"name": "N"}).json()
    n = client.post(f"/api/topologies/{t['id']}/nodes/", json={"kind": "bridge", "icon": "cloud"}).json()
    assert n["name"] == "NET1" and n["network"]["mode"] == "access" and n["interfaces"] == []
    r = client.patch(f"/api/topologies/{t['id']}/nodes/{n['id']}", json={"network": {"mode": "trunk", "nic": "eno2", "vlan": 7}})
    assert r.status_code == 200 and r.json()["network_label"] == "eno2 · VLAN 7"
    bad = client.patch(f"/api/topologies/{t['id']}/nodes/{n['id']}", json={"network": {"mode": "access", "nic": "eno1"}})
    assert bad.status_code == 400


def test_lab_network_api(client, db, monkeypatch):
    monkeypatch.setattr(lab_network, "ensure_mgmt", lambda s: None)
    monkeypatch.setattr(host_net, "list_plabs_bridges", lambda: [])
    body = dict(mode="bridged", nic="eno2", vlan=0, subnet="10.50.0.0/24", gateway="10.50.0.1",
                host_ip="", pool_start="10.50.0.100", pool_end="10.50.0.199", mtu=0)
    r = client.put("/api/lab-network/", json=body)
    assert r.status_code == 200, r.text
    got = client.get("/api/lab-network/").json()
    assert got["settings"]["nic"] == "eno2" and got["settings"]["mode"] == "bridged"
    assert client.put("/api/lab-network/", json={**body, "gateway": "10.50.0.150"}).status_code == 400
