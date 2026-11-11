"""Site templates: validation of the definition and dropping a site (nodes,
links, patch panels) into a topology in one transaction."""
import pytest

from app.models.link import Link
from app.models.node_instance import NodeInstance
from app.models.node_template import NodeTemplate
from app.models.patch_panel import PatchPanel
from app.services.site_templates import SiteTemplateError, instantiate, normalize_definition
from tests.factories import make_topology, make_node

IFACES = [{"name": f"Gi0/{i}", "speed_gbps": 10} for i in range(4)] + [{"name": "Hu0/0", "speed_gbps": 100}]


def _device_template(db):
    t = NodeTemplate(name="PE", vendor="cisco", model="8201", platform="iosxr",
                     capacity_gbps=400, default_mtbf_hours=1000, default_mttr_hours=4)
    t.default_interfaces = IFACES
    db.add(t)
    db.flush()
    return t


def _definition(t, **kw):
    d = {
        "variables": [{"name": "site", "label": "Site code"},
                      {"name": "location", "label": "Location"},
                      {"name": "cid", "required": False, "default": "TBD"}],
        "nodes": [
            {"key": "n1", "template_id": t.id, "name": "{{site}}-pe1", "location": "{{location}}", "dx": 0, "dy": 0},
            {"key": "n2", "template_id": t.id, "name": "{{site}}-pe2", "location": "{{location}}", "dx": 150, "dy": 0},
        ],
        "links": [{"key": "l1", "source": "n1", "source_interface": "Gi0/0",
                   "target": "n2", "target_interface": "Gi0/0", "circuit_id": "{{cid}}"}],
        "patch_panels": [
            {"key": "front", "name": "PP-{{site}}-A", "location": "{{location}}", "in_front_of": "wan",
             "ports": {"1": {"status": "cabled_used", "node": "n1", "interface": "Gi0/1", "rear_port": 3},
                       "2": {"status": "cabled_free", "node": "n1", "link": "l1"}}},
            {"key": "wan", "name": "PP-{{site}}-WAN", "location": "{{location}}", "ports": {}},
        ],
    }
    d.update(kw)
    return d


def test_normalize_fills_keys_and_link_interface(db):
    t = _device_template(db)
    d = normalize_definition(db, _definition(t))
    assert [n["key"] for n in d["nodes"]] == ["n1", "n2"]
    # A port documenting a link picks up that link's interface on the device.
    assert d["patch_panels"][0]["ports"]["2"]["interface"] == "Gi0/0"


@pytest.mark.parametrize("mutate, msg", [
    (lambda d: d["nodes"].extend([dict(d["nodes"][0], key=f"x{i}") for i in range(3)]), "at most 4"),
    (lambda d: d["links"][0].update(target_interface="Hu0/0"), "speed mismatch"),
    (lambda d: d["links"][0].update(target="n1"), "two different devices"),
    (lambda d: d["nodes"][0].update(name="{{nope}}-pe1"), "{{nope}}"),
    (lambda d: d["patch_panels"][1]["ports"].update({"1": {"node": "n1"}}), "wan-facing"),
    (lambda d: d["patch_panels"][0]["ports"]["2"].update(rear_port=4), "can't run through chained panels"),
    (lambda d: d["patch_panels"][0]["ports"].update({"5": {"node": "n2", "rear_port": 3}}), "more than one port"),
    (lambda d: d["patch_panels"][1].update(in_front_of="front"), "loop"),
    (lambda d: d["nodes"][0].update(reserved_interfaces=["Gi9/9"]), "no such interface"),
    (lambda d: d["nodes"][0].update(reserved_interfaces=["Gi0/0"]), "used by a link"),
])
def test_normalize_rejects(db, mutate, msg):
    t = _device_template(db)
    d = _definition(t)
    mutate(d)
    with pytest.raises(SiteTemplateError, match=msg.replace("{", r"\{").replace("}", r"\}")):
        normalize_definition(db, d)


def test_instantiate_creates_site(db):
    t = _device_template(db)
    topo = make_topology(db)
    db.commit()
    d = normalize_definition(db, _definition(t))
    out = instantiate(db, topo.id, d, {"site": "NYC1", "location": "New York"}, 100, 200, panels_enabled=True)

    names = sorted(n.name for n in db.query(NodeInstance).filter_by(topology_id=topo.id))
    assert names == ["NYC1-pe1", "NYC1-pe2"]
    pe1, pe2 = out["nodes"]
    assert (pe1.x, pe1.y, pe2.x) == (100, 200, 250)
    assert pe1.location == "New York" and pe1.vendor == "cisco" and len(pe1.interfaces) == len(IFACES)
    link = db.query(Link).one()
    assert (link.source_interface, link.bandwidth_gbps, link.cost) == ("Gi0/0", 10, 40)
    assert link.circuit_id == "TBD"  # optional variable falls back to its default

    panels = {p.name: p for p in db.query(PatchPanel).all()}
    front, wan = panels["PP-NYC1-A"], panels["PP-NYC1-WAN"]
    assert front.in_front_of_id == wan.id and front.location == "New York"
    assert front.ports["1"]["node_id"] == pe1.id and front.ports["1"]["rear_port"] == 3
    assert front.ports["2"]["link_id"] == link.id
    # The jumper's far end exists and shares the path's status.
    assert wan.ports["3"]["status"] == "cabled_used"
    assert out["warnings"] == []


def test_reserved_interfaces(db):
    t = _device_template(db)
    topo = make_topology(db)
    db.commit()
    raw = _definition(t)
    raw["nodes"][0]["reserved_interfaces"] = ["Gi0/2", "Gi0/3", "Gi0/2", ""]
    d = normalize_definition(db, raw)
    assert d["nodes"][0]["reserved_interfaces"] == ["Gi0/2", "Gi0/3"]
    assert d["nodes"][1]["reserved_interfaces"] == []  # missing key -> none reserved
    pe1, pe2 = instantiate(db, topo.id, d, {"site": "NYC1", "location": "NY"}, 0, 0, panels_enabled=True)["nodes"]
    status = {i["name"]: i["status"] for i in pe1.interfaces}
    assert status == {"Gi0/0": "free", "Gi0/1": "free", "Gi0/2": "reserved", "Gi0/3": "reserved", "Hu0/0": "free"}
    assert all(i["status"] == "free" for i in pe2.interfaces)


def test_instantiate_requires_variables_and_free_names(db):
    t = _device_template(db)
    topo = make_topology(db)
    make_node(db, topo, "NYC1-pe2")
    db.commit()
    d = normalize_definition(db, _definition(t))
    with pytest.raises(SiteTemplateError, match="Fill in: Site code"):
        instantiate(db, topo.id, d, {"location": "X"}, 0, 0, panels_enabled=True)
    with pytest.raises(SiteTemplateError, match="NYC1-pe2") as e:
        instantiate(db, topo.id, d, {"site": "NYC1", "location": "X"}, 0, 0, panels_enabled=True)
    assert e.value.status == 409
    db.rollback()
    assert db.query(NodeInstance).count() == 1


def test_instantiate_skips_panels_when_unavailable(db):
    t = _device_template(db)
    root = make_topology(db, "root")
    child = make_topology(db, "child", parent_id=root.id)
    db.commit()
    d = normalize_definition(db, _definition(t))
    out = instantiate(db, child.id, d, {"site": "A", "location": "L"}, 0, 0, panels_enabled=True)
    assert len(out["nodes"]) == 2 and db.query(PatchPanel).count() == 0
    assert "planning layers" in out["warnings"][0]
    out = instantiate(db, root.id, d, {"site": "B", "location": "L"}, 0, 0, panels_enabled=False)
    assert db.query(PatchPanel).count() == 0 and "module is off" in out["warnings"][0]


def test_api_crud_and_instantiate(client, db):
    t = _device_template(db)
    topo = make_topology(db)
    db.commit()
    r = client.post("/api/site-templates/", json={"name": "Small PoP", "definition": _definition(t, patch_panels=[])})
    assert r.status_code == 200, r.text
    tid = r.json()["id"]
    assert client.post("/api/site-templates/", json={"name": "Small PoP", "definition": _definition(t)}).status_code == 409
    bad = _definition(t)
    bad["links"][0]["source_interface"] = "Gi9/9"
    assert client.put(f"/api/site-templates/{tid}", json={"name": "Small PoP", "definition": bad}).status_code == 400

    r = client.post(f"/api/topologies/{topo.id}/site-templates/{tid}/instantiate",
                    json={"variables": {"site": "SEA", "location": "Seattle"}, "x": 10, "y": 20})
    assert r.status_code == 200, r.text
    body = r.json()
    assert sorted(n["name"] for n in body["nodes"]) == ["SEA-pe1", "SEA-pe2"] and len(body["links"]) == 1
    assert client.delete(f"/api/site-templates/{tid}").status_code == 200
    assert client.get("/api/site-templates/").json() == []
