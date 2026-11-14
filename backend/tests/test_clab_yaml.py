import yaml

from app.services.clab_yaml import generate_clab_yaml
from tests.factories import make_link, make_node, make_topology


def _doc(db, t):
    return yaml.safe_load(generate_clab_yaml(db, t.id)["yaml"])


def test_yaml_uses_node_kind_image_and_kernel_names(db):
    t = make_topology(db, "Core Lab")
    p1 = make_node(db, t, name="P1", kind="cisco_xrd", image="ios-xr/xrd-control-plane:25.4.2")
    pe = make_node(db, t, name="PE1", kind="cisco_c8000v", image="vrnetlab/cisco_c8000v:17.15")
    make_link(db, t, p1, pe)
    doc = _doc(db, t)
    assert doc["name"] == f"plabs-{t.id}-Core-Lab"
    assert doc["topology"]["nodes"]["P1"] == {"kind": "cisco_xrd", "image": "ios-xr/xrd-control-plane:25.4.2"}
    # Every port is pre-provisioned as a host half; cables are host patches.
    links = [l["endpoints"] for l in doc["topology"]["links"]]
    assert links[0] == ["P1:Gi0-0-0-0", f"host:pp{t.id}n{p1.id}p0"]
    assert len(links) == len(p1.interfaces) + len(pe.interfaces)
    assert ["PE1:eth1", f"host:pp{t.id}n{pe.id}p0"] in links
    spec = generate_clab_yaml(db, t.id)
    assert spec["patches"] == [{"a": f"pp{t.id}n{p1.id}p0", "b": f"pp{t.id}n{pe.id}p0"}]
    assert len(spec["ports"]) == len(links)


def test_network_node_is_a_host_bridge_patch(db):
    t = make_topology(db)
    a = make_node(db, t, name="A", kind="linux", image="frr")
    sw = make_node(db, t, name="SW", kind="bridge", icon="switch")
    make_link(db, t, a, sw)
    spec = generate_clab_yaml(db, t.id)
    assert "SW" not in yaml.safe_load(spec["yaml"])["topology"]["nodes"]
    assert spec["patches"] == [{"a": f"pp{t.id}n{a.id}p0", "bridge": f"pl-sw{t.id}-{sw.id}"}]


def test_no_host_port_publishing(db):
    t = make_topology(db)
    make_node(db, t, kind="cisco_xrd", image="x")
    for n in _doc(db, t)["topology"]["nodes"].values():
        assert "ports" not in n


def test_nodes_without_image_are_skipped_with_their_links(db):
    t = make_topology(db)
    a = make_node(db, t, name="A")
    b = make_node(db, t, name="B", image="")
    make_link(db, t, a, b)
    spec = generate_clab_yaml(db, t.id)
    assert [s["name"] for s in spec["skipped_nodes"]] == ["B"]
    assert spec["included_links"] == []
    assert list(yaml.safe_load(spec["yaml"])["topology"]["nodes"]) == ["A"]


def test_xrd_startup_config_keeps_clab_defaults(db, tmp_path, monkeypatch):
    import app.services.clab_yaml as cy
    monkeypatch.setattr(cy, "LAB_STATE_DIR", tmp_path)
    t = make_topology(db)
    make_node(db, t, name="P1", kind="cisco_xrd", image="x", startup_config="interface Loopback0\n ipv4 address 1.1.1.1/32")
    spec = generate_clab_yaml(db, t.id)
    ref = yaml.safe_load(spec["yaml"])["topology"]["nodes"]["P1"]["startup-config"]
    assert ref.startswith("configs/P1-") and ref.endswith(".cfg")
    body = open(spec["rendered_config_paths"][0]).read()
    assert "username clab" in body and "ssh server netconf" in body
    assert "ipv4 address 1.1.1.1/32" in body and body.rstrip().endswith("end")


def test_resource_estimate_uses_kind_defaults(db):
    t = make_topology(db)
    make_node(db, t, kind="cisco_xrd", image="x")
    make_node(db, t, kind="cisco_xrd", image="ios-xr/xrd-vrouter:25.4.2")
    est = generate_clab_yaml(db, t.id)["resource_estimate"]
    assert est["memory_mb"] == 4096 and est["requires_hugepages"] is True


def test_changed_startup_config_changes_yaml(db, tmp_path, monkeypatch):
    import app.services.clab_yaml as cy
    monkeypatch.setattr(cy, "LAB_STATE_DIR", tmp_path)
    t = make_topology(db)
    n = make_node(db, t, name="A", image="plabs/frr:latest", startup_config="hostname a")
    y1 = generate_clab_yaml(db, t.id)["yaml"]
    n.startup_config = "hostname b"
    db.flush()
    y2 = generate_clab_yaml(db, t.id)["yaml"]
    assert y1 != y2
    assert len(list((tmp_path / f"plabs-{t.id}-T" / "configs").glob("A-*.cfg"))) == 1
