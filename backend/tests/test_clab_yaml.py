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
    p1_entry = doc["topology"]["nodes"]["P1"]
    assert p1_entry["kind"] == "cisco_xrd" and p1_entry["image"] == "ios-xr/xrd-control-plane:25.4.2"
    assert p1_entry["startup-config"].startswith("configs/P1-")       # carries the admin/cisco1 login
    assert doc["topology"]["nodes"]["PE1"]["env"] == {"USERNAME": "admin", "PASSWORD": "cisco1"}
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
    assert "username admin\n" in body and " secret cisco1\n" in body and "ssh server netconf" in body
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


def test_frr_startup_config_is_mounted_and_shell_lines_run(db, tmp_path, monkeypatch):
    """containerlab ignores startup-config on Linux nodes: an FRR node's config
    is mounted as /etc/frr/frr.conf, and `$ ` lines become exec commands."""
    import yaml
    import app.services.clab_yaml as cy
    monkeypatch.setattr(cy, "LAB_STATE_DIR", tmp_path)
    t = make_topology(db)
    make_node(db, t, name="A", image="plabs/frr:latest",
              startup_config="$ ip link set eth1 up\nrouter isis CORE\n net 49.0001.0000.0000.0001.00\n$ echo 'hi'")
    entry = yaml.safe_load(generate_clab_yaml(db, t.id)["yaml"])["topology"]["nodes"]["A"]
    assert "startup-config" not in entry
    assert entry["exec"] == ["sh -c 'ip link set eth1 up'", "sh -c 'echo '\"'\"'hi'\"'\"''"]
    (bind,) = entry["binds"]
    src, dst = bind.split(":")
    assert dst == "/etc/frr/frr.conf" and src.startswith("configs/A-")
    text = (tmp_path / f"plabs-{t.id}-T" / src).read_text()
    assert "router isis CORE" in text and "ip link" not in text
    assert cy.frr_startup("linux", "plabs/host:latest", "x") is None


def test_devices_made_before_provisioning_keep_the_image_login(db):
    """Devices with no stored credentials predate provisioning: their YAML
    must not change (that would recreate them) and the app logs in with the
    image's own credentials."""
    from app.services.lab_view import node_credentials
    t = make_topology(db)
    p1 = make_node(db, t, name="P1", kind="cisco_xrd", image="x")
    pe = make_node(db, t, name="PE1", kind="cisco_csr1000v", image="vrnetlab/csr")
    assert node_credentials(p1) == ("admin", "cisco1") and node_credentials(pe) == ("admin", "cisco1")
    p1.credentials, pe.credentials = {}, {}
    db.commit()
    nodes = _doc(db, t)["topology"]["nodes"]
    assert nodes["P1"] == {"kind": "cisco_xrd", "image": "x"} and "env" not in nodes["PE1"]
    assert node_credentials(p1) == ("clab", "clab@123") and node_credentials(pe) == ("admin", "admin")


def test_editing_the_login_never_changes_the_device(db):
    from app.services.lab_view import node_credentials
    t = make_topology(db)
    pe = make_node(db, t, name="PE1", kind="cisco_csr1000v", image="vrnetlab/csr")
    before = generate_clab_yaml(db, t.id)["yaml"]
    pe.credentials = {**pe.credentials, "username": "netops", "password": "x"}
    db.commit()
    assert generate_clab_yaml(db, t.id)["yaml"] == before
    assert node_credentials(pe) == ("netops", "x")


def test_captured_starting_config_drops_the_platform_login():
    from app.services.lessons import starting_config_from_running
    xr = "username admin\n group root-lr\n secret 10 $6$abc\n!\nusername admin2\n group root-lr\n!\nrouter isis 1\n!\n"
    out = starting_config_from_running("cisco_xrd", xr)
    assert "username admin\n" not in out and "username admin2" in out and "router isis 1" in out
    xe = "username admin privilege 15 password 0 cisco1\nusername bob secret 0 x\nrouter ospf 1\n"
    out = starting_config_from_running("cisco_csr1000v", xe)
    assert "username admin " not in out and "username bob" in out
