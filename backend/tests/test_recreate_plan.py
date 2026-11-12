import yaml

from app.services.lab_service import _recreate_plan, _startup_changed


def doc(nodes, links):
    return yaml.safe_dump({"name": "t", "topology": {
        "nodes": nodes, "links": [{"endpoints": list(e)} for e in links]}})


XRD = {"kind": "cisco_xrd", "image": "xrd:1"}
C8K = {"kind": "cisco_c8000v", "image": "vrnetlab/c8k:1"}
FRR = {"kind": "linux", "image": "frr:1"}


def test_new_link_recreates_only_non_live_kinds():
    old = doc({"R1": XRD, "R2": XRD, "F1": FRR}, [("R1:Gi0-0-0-0", "R2:Gi0-0-0-0")])
    new = doc({"R1": XRD, "R2": XRD, "F1": FRR, "R3": C8K},
              [("R1:Gi0-0-0-0", "R2:Gi0-0-0-0"), ("R3:eth1", "R1:Gi0-0-0-1"), ("F1:eth1", "R2:Gi0-0-0-1")])
    # R3 is new (containerlab creates it); F1 is linux → link added live
    assert sorted(_recreate_plan(old, new)) == ["R1", "R2"]


def test_removed_link_and_node():
    old = doc({"R1": XRD, "R2": XRD, "R3": C8K, "F1": FRR},
              [("R1:Gi0-0-0-0", "R2:Gi0-0-0-0"), ("R3:eth1", "R1:Gi0-0-0-1"), ("F1:eth1", "R3:eth2")])
    new = doc({"R1": XRD, "R2": XRD, "F1": FRR}, [("R1:Gi0-0-0-0", "R2:Gi0-0-0-0")])
    assert _recreate_plan(old, new) == ["R1"]


def test_unchanged_and_startup_only():
    old = doc({"R1": {**XRD, "startup-config": "configs/R1-aaaa.cfg"}, "B": {"kind": "bridge"}},
              [("R1:Gi0-0-0-0", "B:p1")])
    new = doc({"R1": {**XRD, "startup-config": "configs/R1-bbbb.cfg"}, "B": {"kind": "bridge"}},
              [("R1:Gi0-0-0-0", "B:p1")])
    assert _recreate_plan(old, old) == []
    assert _recreate_plan(old, new) == []           # handled by _startup_changed
    assert _startup_changed(old, new) == ["R1"]


def test_image_change_recreates_any_kind():
    old = doc({"F1": FRR}, [])
    new = doc({"F1": {**FRR, "image": "frr:2"}}, [])
    assert _recreate_plan(old, new) == ["F1"]


def test_patched_links_isolate_neighbours(db):
    """A cable touching XRd / VMs becomes two host halves, so a device whose
    own cables change is the only one containerlab recreates."""
    from app.models.topology import Topology
    from app.services import topology_ops
    from app.services.clab_yaml import generate_clab_yaml
    t = Topology(name="w"); db.add(t); db.commit()
    r1 = topology_ops.create_node(db, t.id, kind="cisco_xrd", image_ref="xrd:1")
    r2 = topology_ops.create_node(db, t.id, kind="cisco_xrd", image_ref="xrd:1")
    f1 = topology_ops.create_node(db, t.id, kind="linux", image_ref="frr:1")
    f2 = topology_ops.create_node(db, t.id, kind="linux", image_ref="frr:1")
    l12 = topology_ops.create_link(db, t.id, r1.id, r2.id)
    topology_ops.create_link(db, t.id, f1.id, f2.id)
    db.commit()
    old = generate_clab_yaml(db, t.id)
    assert len(old["wires"]) == 1 and old["wires"][0][0].endswith("a")
    links = yaml.safe_load(old["yaml"])["topology"]["links"]
    assert [l["endpoints"][1].startswith("host:") for l in links] == [True, True, False]

    r3 = topology_ops.create_node(db, t.id, kind="cisco_xrd", image_ref="xrd:1")
    topology_ops.create_link(db, t.id, r3.id, r1.id)
    db.commit()
    new = generate_clab_yaml(db, t.id)
    assert _recreate_plan(old["yaml"], new["yaml"]) == [r1.name]   # not R2
    assert len(new["wires"]) == 2


def test_day0_config_strips_unsafe_xe_lines():
    from app.services.clab_yaml import day0_config
    run = """Building configuration...

Current configuration : 6270 bytes
!
version 17.18
platform sslvpn use-pd
hostname C1
!
crypto pki trustpoint TP-self-signed-123
 enrollment selfsigned
 revocation-check none
!
crypto pki certificate chain TP-self-signed-123
 certificate self-signed 01
  30820330 30820218
  \tquit
interface Loopback0
 ip address 5.5.5.5 255.255.255.255
interface GigabitEthernet3
 shutdown
license udi pid C8000V sn X
end
"""
    out = day0_config("iosxe", run)
    assert "crypto pki" not in out and "30820330" not in out and "quit" not in out
    assert "version" not in out and "platform" not in out and "license udi" not in out
    assert "hostname C1" in out and " ip address 5.5.5.5 255.255.255.255" in out
    assert " ip address 5.5.5.5 255.255.255.255\n no shutdown\ninterface GigabitEthernet3\n shutdown\n" in out + "\n"
    assert out.count("no shutdown") == 1 and out.rstrip().endswith("\nend")
    assert day0_config("iosxr", run) == run


def test_day0_leaves_empty_config_empty():
    from app.services.clab_yaml import day0_config
    assert day0_config("iosxe", "") == ""


def test_remove_for_recreate_wipes_only_new_startup(tmp_path, monkeypatch):
    from app.services import lab_service
    monkeypatch.setattr(lab_service, "_lab_dir_for", lambda name: tmp_path / name)
    monkeypatch.setattr(lab_service, "_run", lambda *a, **k: (0, "", ""))
    for n in ("C1", "R1"):
        (tmp_path / "lab" / "clab-lab" / n / "config").mkdir(parents=True)
    lab_service._remove_for_recreate("lab", ["C1", "R1"], wipe={"C1"})
    assert not (tmp_path / "lab" / "clab-lab" / "C1").exists()
    assert (tmp_path / "lab" / "clab-lab" / "R1" / "config").exists()   # XRd keeps xr-storage on link changes
