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


@pytest.fixture
def fake_dhcp(monkeypatch):
    """DHCP server on 10.24.110.0/23 handing out sequential addresses."""
    from app.services import dhcp
    state = {"next": 50, "by_client": {}}

    def acquire(iface, client_id, hostname="", requested_ip="", **kw):
        if client_id not in state["by_client"]:
            state["by_client"][client_id] = f"10.24.110.{state['next']}"
            state["next"] += 1
        return dhcp.Lease(ip=state["by_client"][client_id], prefix=23, router="10.24.110.1",
                          dns=["10.24.110.1"], server_id="10.24.110.1", lease_time=86400, obtained_at=1e12)

    monkeypatch.setattr(dhcp, "acquire", acquire)
    monkeypatch.setattr(dhcp, "release", lambda *a, **k: None)
    monkeypatch.setattr(host_net, "ensure_bridge", lambda *a, **k: None)
    monkeypatch.setattr(host_net, "ensure_host_address", lambda *a, **k: None)
    monkeypatch.setattr(lab_network, "_docker_network_subnet", lambda name: "10.24.110.0/23")
    return state


def test_dhcp_message_round_trip():
    from app.services import dhcp
    req = dhcp.build_message(dhcp.DISCOVER, 0xABCD, "02:00:00:00:00:01", "plabs:h:node:1", "R1", "10.0.0.5")
    assert len(req) >= 300 and req[236:240] == dhcp.MAGIC
    # Turn the request into an ACK-shaped reply and parse it.
    import socket, struct
    reply = bytearray(req)
    reply[0] = 2
    reply[16:20] = socket.inet_aton("10.24.110.77")
    opts = (dhcp.MAGIC + bytes([53, 1, dhcp.ACK, 1, 4]) + socket.inet_aton("255.255.254.0")
            + bytes([3, 4]) + socket.inet_aton("10.24.110.1") + bytes([51, 4]) + struct.pack("!I", 7200)
            + bytes([54, 4]) + socket.inet_aton("10.24.110.1") + b"\xff")
    msg = dhcp.parse_message(bytes(reply[:236]) + opts)
    lease = dhcp.lease_from(msg)
    assert (msg["xid"], msg["type"]) == (0xABCD, dhcp.ACK)
    assert (lease.ip, lease.prefix, lease.router, lease.lease_time) == ("10.24.110.77", 23, "10.24.110.1", 7200)
    assert lease.subnet == "10.24.110.0/23"


def test_per_device_mac_is_stable_and_local():
    from app.services import dhcp
    a, b = dhcp.mac_for("x:1"), dhcp.mac_for("x:2")
    assert a == dhcp.mac_for("x:1") and a != b
    assert int(a.split(":")[0], 16) & 0x03 == 0x02     # locally administered, unicast


def _mgmt_lab(db):
    t = make_topology(db)
    r1 = make_node(db, t, name="R1", kind="cisco_xrd", image="xrd")
    r2 = make_node(db, t, name="R2", kind="cisco_xrd", image="xrd")
    lan = make_node(db, t, name="LAB-LAN", kind="bridge", icon="cloud")
    set_network(db, lan, {"mode": "access", "nic": "eno2", "mgmt": True})
    return t, r1, r2, lan


def test_mgmt_network_node_drives_yaml_and_leases(db, fake_dhcp):
    t, r1, r2, lan = _mgmt_lab(db)
    make_link(db, t, r1, lan)                       # a data port onto the same network, too
    spec = generate_clab_yaml(db, t.id, assign_ips=True)
    doc = yaml.safe_load(spec["yaml"])
    assert doc["mgmt"] == {"network": "plabs-m-pl-eno2", "ipv4-subnet": "10.24.110.0/23", "ipv4-gw": "10.24.110.1"}
    nodes = doc["topology"]["nodes"]
    ips = {nodes["R1"]["mgmt-ipv4"], nodes["R2"]["mgmt-ipv4"]}
    assert len(ips) == 2 and all(ip.startswith("10.24.110.") for ip in ips)
    assert nodes["pl-eno2"] == {"kind": "bridge"}
    # the XRd mgmt default route points at the learned gateway
    # sticky: a second generation keeps the same addresses
    again = yaml.safe_load(generate_clab_yaml(db, t.id, assign_ips=True)["yaml"])["topology"]["nodes"]
    assert {again["R1"]["mgmt-ipv4"], again["R2"]["mgmt-ipv4"]} == ips
    # previews never touch DHCP
    fake_dhcp["next"] = 999
    generate_clab_yaml(db, t.id)


def test_lab_without_mgmt_node_uses_internal(db):
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


def test_mgmt_flag_rules(db):
    t = make_topology(db)
    a = make_node(db, t, kind="bridge", icon="cloud")
    b = make_node(db, t, kind="bridge", icon="cloud")
    sw = make_node(db, t, kind="bridge", icon="switch")
    set_network(db, a, {"mode": "access", "nic": "eno2", "mgmt": True})
    with pytest.raises(TopologyError, match="already this lab's management"):
        set_network(db, b, {"mode": "trunk", "nic": "eno2", "vlan": 5, "mgmt": True})
    with pytest.raises(TopologyError, match="physical NIC"):
        set_network(db, sw, {"mode": "internal", "mgmt": True})
    assert lab_network.lab_mgmt(db, t.id).bridge == "pl-eno2"


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
    monkeypatch.setattr(host_net, "list_plabs_bridges", lambda: [])
    monkeypatch.setattr(lab_network, "mgmt_docker_networks", lambda: {})
    r = client.put("/api/lab-network/", json={"subnet": "10.77.0.0/22", "mtu": 0})
    assert r.status_code == 200, r.text
    got = client.get("/api/lab-network/").json()
    assert got["settings"]["subnet"] == "10.77.0.0/22"
    assert client.put("/api/lab-network/", json={"subnet": "nope", "mtu": 0}).status_code == 400
