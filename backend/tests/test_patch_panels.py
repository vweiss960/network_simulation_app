"""Patch panels: location-scoped documentation of how EXISTING links reach a
device through a panel. Route functions are called directly — the module's
router isn't registered in the default test app (flag off)."""
import json

import pytest
from fastapi import HTTPException

from app.models.link import Link
from app.models.node_instance import NodeInstance
from app.models.patch_panel import PatchPanel
from app.routes.patch_panels import (
    PanelCreate, PanelUpdate, PortUpdate,
    create_panel, list_panels, update_panel, update_port, delete_panel,
)
from app.routes.topologies import _serialize_topology, _import_topology_data
from tests.factories import make_topology, make_node, make_link

IFACES = [{"name": f"Gi0/{i}", "speed_gbps": 10} for i in range(4)]


def _seed(db):
    topo = make_topology(db, "root")
    a = make_node(db, topo, "PE-A", interfaces=IFACES, location="DC1")
    b = make_node(db, topo, "PE-B", interfaces=IFACES, location="DC1")
    c = make_node(db, topo, "PE-C", interfaces=IFACES, location="DC2")
    cust = make_node(db, topo, "Cust-1", role="customer", interfaces=IFACES)
    cust_link = make_link(db, topo, a, cust, source_interface="Gi0/1", target_interface="Gi0/0")
    wan = make_link(db, topo, a, c, source_interface="Gi0/2", target_interface="Gi0/2",
                    carrier="Lumen", circuit_id="CID-9")
    db.commit()
    return topo, a, b, c, cust, cust_link, wan


def _panel(db, topo, **kw):
    args = dict(location="DC1", name="PP-1", rack="R12", start_u=20, height_u=2,
                rows=2, ports_per_row=24)
    args.update(kw)
    return create_panel(topo.id, PanelCreate(**args), db)


def test_panels_are_shared_by_location(db):
    topo, a, b, c, *_ = _seed(db)
    _panel(db, topo)
    # Visible from any DC1 device (same location query), not from DC2.
    assert [p["name"] for p in list_panels(topo.id, a.location, db)] == ["PP-1"]
    assert [p["name"] for p in list_panels(topo.id, b.location, db)] == ["PP-1"]
    assert list_panels(topo.id, c.location, db) == []


def test_port_links_existing_link_and_autofills_device_side(db):
    topo, a, b, c, cust, cust_link, wan = _seed(db)
    p = _panel(db, topo)
    out = update_port(topo.id, p["id"], 3,
                      PortUpdate(status="cabled_used", link_id=cust_link.id, notes="XC-1"), db)
    port = out["ports"]["3"]
    assert port["node_id"] == a.id and port["interface"] == "Gi0/1"
    assert port["link"]["far_node"] == "Cust-1" and not port["missing"]
    # WAN link details surface for display.
    out = update_port(topo.id, p["id"], 4, PortUpdate(status="cabled_used", link_id=wan.id), db)
    assert out["ports"]["4"]["link"]["circuit_id"] == "CID-9"
    # Pre-patched port: device side only, no link.
    out = update_port(topo.id, p["id"], 5,
                      PortUpdate(status="cabled_free", node_id=b.id, interface="Gi0/3"), db)
    assert out["ports"]["5"]["node_name"] == "PE-B"


@pytest.mark.parametrize("bad", [
    dict(node_id="C"),                         # device at another location
    dict(node_id="A", interface="Gi9/9"),      # unknown interface
    dict(node_id="B", link="cust"),            # link not on that device
    dict(link=99999),                          # link doesn't exist
    dict(status="broken"),                     # bad status
    dict(port=49),                             # beyond 2x24
])
def test_port_validation(db, bad):
    topo, a, b, c, cust, cust_link, wan = _seed(db)
    p = _panel(db, topo)
    nodes = {"A": a.id, "B": b.id, "C": c.id}
    link = {"cust": cust_link.id}.get(bad.get("link"), bad.get("link"))
    with pytest.raises(HTTPException) as e:
        update_port(topo.id, p["id"], bad.get("port", 1), PortUpdate(
            status=bad.get("status", "cabled_used"),
            node_id=nodes.get(bad.get("node_id")), interface=bad.get("interface", ""),
            link_id=link), db)
    assert e.value.status_code == 400


def test_unused_empty_port_is_removed(db):
    topo, a, *_ = _seed(db)
    p = _panel(db, topo)
    update_port(topo.id, p["id"], 1, PortUpdate(status="cabled_reserved", notes="hold"), db)
    out = update_port(topo.id, p["id"], 1, PortUpdate(status="unused"), db)
    assert out["ports"] == {}


def test_shrinking_with_data_needs_force(db):
    topo, *_ = _seed(db)
    p = _panel(db, topo)
    update_port(topo.id, p["id"], 40, PortUpdate(status="cabled_reserved"), db)
    with pytest.raises(HTTPException) as e:
        update_panel(topo.id, p["id"], PanelUpdate(rows=1), force=False, db=db)
    assert e.value.status_code == 409 and e.value.detail["ports"] == [40]
    out = update_panel(topo.id, p["id"], PanelUpdate(rows=1), force=True, db=db)
    assert out["rows"] == 1 and out["ports"] == {}


def test_child_topology_rejected(db):
    topo, *_ = _seed(db)
    child = make_topology(db, "child", parent_id=topo.id)
    db.commit()
    with pytest.raises(HTTPException) as e:
        list_panels(child.id, "DC1", db)
    assert e.value.status_code == 400


def test_deleting_link_or_node_clears_refs_but_keeps_record(db):
    from app.routes.links import delete_link
    from app.routes.nodes import delete_node
    topo, a, b, c, cust, cust_link, wan = _seed(db)
    p = _panel(db, topo)
    update_port(topo.id, p["id"], 1, PortUpdate(status="cabled_used", link_id=cust_link.id, notes="XC-1"), db)
    update_port(topo.id, p["id"], 2, PortUpdate(status="cabled_free", node_id=b.id, interface="Gi0/0"), db)

    delete_link(topo.id, cust_link.id, db)
    ports = list_panels(topo.id, "DC1", db)[0]["ports"]
    assert ports["1"]["link_id"] is None and ports["1"]["dangling"]
    assert ports["1"]["status"] == "cabled_used" and ports["1"]["notes"] == "XC-1"

    delete_node(topo.id, b.id, db)
    ports = list_panels(topo.id, "DC1", db)[0]["ports"]
    assert ports["2"]["node_id"] is None and ports["2"]["status"] == "cabled_free"


def test_export_import_round_trip(db):
    topo, a, b, c, cust, cust_link, wan = _seed(db)
    p = _panel(db, topo, notes="front of PE-A")
    update_port(topo.id, p["id"], 3, PortUpdate(status="cabled_used", link_id=cust_link.id,
                                                label="A3", notes="XC-1"), db)
    update_port(topo.id, p["id"], 5, PortUpdate(status="cabled_free", node_id=b.id, interface="Gi0/3"), db)

    snap = json.loads(json.dumps(_serialize_topology(topo.id, db)))  # JSON-safe
    new = _import_topology_data(snap, db)
    [pp] = list_panels(new.id, "DC1", db)
    assert (pp["rack"], pp["start_u"], pp["height_u"], pp["rows"], pp["ports_per_row"], pp["notes"]) \
        == ("R12", 20, 2, 2, 24, "front of PE-A")
    new_a = db.query(NodeInstance).filter_by(topology_id=new.id, name="PE-A").first()
    new_link = db.query(Link).filter_by(topology_id=new.id, source_node_id=new_a.id,
                                        source_interface="Gi0/1").first()
    assert pp["ports"]["3"]["node_id"] == new_a.id and pp["ports"]["3"]["link_id"] == new_link.id
    assert pp["ports"]["3"]["label"] == "A3" and pp["ports"]["3"]["notes"] == "XC-1"
    assert pp["ports"]["5"]["node_name"] == "PE-B" and pp["ports"]["5"]["interface"] == "Gi0/3"


def test_panels_have_no_side_effects_on_topology(db):
    """Documentation only: attaching links to ports and deleting the panel
    leaves links/nodes/interfaces and simulation results untouched."""
    from app.services.simulation import run_simulation
    topo, a, b, c, cust, cust_link, wan = _seed(db)

    def snapshot():
        db.expire_all()
        nodes = [(n.name, n.interfaces_json, n.location) for n in
                 db.query(NodeInstance).filter_by(topology_id=topo.id).order_by(NodeInstance.id)]
        links = [(l.id, l.source_node_id, l.target_node_id, l.source_interface, l.target_interface,
                  l.carrier, l.circuit_id) for l in db.query(Link).filter_by(topology_id=topo.id).order_by(Link.id)]
        return nodes, links, run_simulation(topo.id, db).get("link_utilizations")

    before = snapshot()
    p = _panel(db, topo)
    update_port(topo.id, p["id"], 1, PortUpdate(status="cabled_used", link_id=cust_link.id), db)
    update_port(topo.id, p["id"], 2, PortUpdate(status="cabled_used", link_id=wan.id), db)
    assert snapshot() == before
    delete_panel(topo.id, p["id"], db)
    assert snapshot() == before
    assert db.query(PatchPanel).count() == 0


# ---------------------------------------------------------------- chaining

def _chain(db):
    """FRONT (customer-facing) in front of BACK (wan-facing), both at DC1."""
    topo, a, b, c, cust, cust_link, wan = _seed(db)
    back = _panel(db, topo, name="BACK", rack="R12")
    front = _panel(db, topo, name="FRONT", rack="R1", in_front_of_id=back["id"])
    return topo, a, b, cust_link, wan, front, back


def _ports(db, topo):
    return {p["name"]: p["ports"] for p in list_panels(topo.id, "DC1", db)}


def test_chain_connect_and_rules(db):
    from app.routes.patch_panels import connect_ports, disconnect_port, ConnectionCreate
    topo, a, b, cust_link, wan, front, back = _chain(db)
    out = connect_ports(topo.id, front["id"], ConnectionCreate(port=3, rear_port=7), db)
    assert out["ports"]["3"]["rear_port"] == 7 and out["in_front_of_id"] == back["id"]
    # Device + WAN link on the customer-facing port; the link is mirrored onto
    # the wan-facing port it's jumpered to.
    update_port(topo.id, front["id"], 3, PortUpdate(status="cabled_used", node_id=a.id, link_id=wan.id), db)
    ports = _ports(db, topo)
    assert (ports["FRONT"]["3"]["node_id"], ports["FRONT"]["3"]["interface"]) == (a.id, "Gi0/2")
    assert ports["BACK"]["7"]["link_id"] == wan.id and ports["BACK"]["7"]["status"] == "cabled_used"
    assert ports["BACK"]["7"]["link"]["far_node"] == "PE-C"
    # Editing the wan-facing port's link updates the customer-facing port too;
    # clearing it clears both.
    update_port(topo.id, back["id"], 7, PortUpdate(status="cabled_used", link_id=None), db)
    ports = _ports(db, topo)
    assert ports["FRONT"]["3"]["link_id"] is None and ports["BACK"]["7"]["link_id"] is None
    update_port(topo.id, back["id"], 7, PortUpdate(status="cabled_used", link_id=wan.id), db)
    assert _ports(db, topo)["FRONT"]["3"]["link_id"] == wan.id
    # Wan-facing ports document no device; jumpered paths carry WAN links only.
    with pytest.raises(HTTPException):
        update_port(topo.id, back["id"], 7, PortUpdate(status="cabled_used", node_id=a.id, interface="Gi0/2"), db)
    with pytest.raises(HTTPException):
        update_port(topo.id, front["id"], 3, PortUpdate(status="cabled_used", link_id=cust_link.id), db)
    with pytest.raises(HTTPException):
        update_port(topo.id, back["id"], 8, PortUpdate(status="cabled_used", link_id=cust_link.id), db)
    # One jumper per rear port: re-using rear 7 moves it off port 3.
    out = connect_ports(topo.id, front["id"], ConnectionCreate(port=4, rear_port=7), db)
    assert "rear_port" not in out["ports"]["3"] and out["ports"]["4"]["rear_port"] == 7
    assert out["ports"]["4"]["link_id"] == wan.id   # picked up from the wan-facing port
    out = disconnect_port(topo.id, front["id"], 4, db)
    assert out["ports"]["4"]["link_id"] == wan.id and "rear_port" not in out["ports"]["4"]


def test_status_syncs_across_jumper(db):
    from app.routes.patch_panels import connect_ports, ConnectionCreate
    topo, a, b, cust_link, wan, front, back = _chain(db)
    # Connecting adopts the most advanced status of the two sides.
    update_port(topo.id, back["id"], 7, PortUpdate(status="cabled_reserved"), db)
    update_port(topo.id, front["id"], 3, PortUpdate(status="cabled_free"), db)
    connect_ports(topo.id, front["id"], ConnectionCreate(port=3, rear_port=7), db)
    ports = _ports(db, topo)
    assert ports["FRONT"]["3"]["status"] == ports["BACK"]["7"]["status"] == "cabled_reserved"
    # Editing either side's status (with or without a link) updates the other.
    for panel, port, other, oport, st in [(front, 3, "BACK", "7", "cabled_free"),
                                          (back, 7, "FRONT", "3", "cabled_reserved"),
                                          (front, 3, "BACK", "7", "cabled_used")]:
        update_port(topo.id, panel["id"], port, PortUpdate(status=st), db)
        assert _ports(db, topo)[other][oport]["status"] == st


def test_wan_link_must_match_the_customer_side_device(db):
    from app.routes.patch_panels import connect_ports, ConnectionCreate
    topo, a, b, cust_link, wan, front, back = _chain(db)
    # wan lands on PE-A; a device on PE-B can't be paired with it.
    connect_ports(topo.id, front["id"], ConnectionCreate(port=3, rear_port=7), db)
    update_port(topo.id, front["id"], 3, PortUpdate(status="cabled_used", node_id=b.id, interface="Gi0/0"), db)
    with pytest.raises(HTTPException):
        update_port(topo.id, back["id"], 7, PortUpdate(status="cabled_used", link_id=wan.id), db)
    assert _ports(db, topo)["FRONT"]["3"]["link_id"] is None
    # Same when the jumper itself would join them.
    update_port(topo.id, front["id"], 4, PortUpdate(status="cabled_used", node_id=b.id, interface="Gi0/1"), db)
    update_port(topo.id, back["id"], 8, PortUpdate(status="cabled_used", link_id=wan.id), db)
    with pytest.raises(HTTPException):
        connect_ports(topo.id, front["id"], ConnectionCreate(port=4, rear_port=8), db)


def test_customer_facing_port_can_patch_a_customer_device(db):
    topo, a, b, c, cust, cust_link, wan = _seed(db)
    p = _panel(db, topo)
    # Cust-1 has no location: customer devices can be patched from any site.
    out = update_port(topo.id, p["id"], 2, PortUpdate(status="cabled_used", node_id=cust.id, interface="Gi0/0"), db)
    assert out["ports"]["2"]["node_name"] == "Cust-1"


def test_chain_cannot_loop_or_leave_location(db):
    topo, a, b, cust_link, wan, front, back = _chain(db)
    with pytest.raises(HTTPException):
        update_panel(topo.id, back["id"], PanelUpdate(in_front_of_id=front["id"]), force=False, db=db)
    other = _panel(db, topo, name="ELSEWHERE", location="DC2")
    with pytest.raises(HTTPException):
        update_panel(topo.id, front["id"], PanelUpdate(in_front_of_id=other["id"]), force=False, db=db)


def test_chain_shrink_and_delete_clear_jumpers(db):
    from app.routes.patch_panels import connect_ports, ConnectionCreate
    topo, a, b, cust_link, wan, front, back = _chain(db)
    connect_ports(topo.id, front["id"], ConnectionCreate(port=1, rear_port=40), db)
    # Shrinking BACK below 40 drops a port with an incoming jumper -> 409.
    with pytest.raises(HTTPException) as e:
        update_panel(topo.id, back["id"], PanelUpdate(rows=1), force=False, db=db)
    assert e.value.detail["ports"] == [40]
    update_panel(topo.id, back["id"], PanelUpdate(rows=1), force=True, db=db)
    assert "1" not in {p["name"]: p for p in list_panels(topo.id, "DC1", db)}["FRONT"]["ports"]
    # Deleting BACK unchains FRONT.
    connect_ports(topo.id, front["id"], ConnectionCreate(port=2, rear_port=5), db)
    delete_panel(topo.id, back["id"], db)
    [f] = list_panels(topo.id, "DC1", db)
    assert f["in_front_of_id"] is None and f["ports"] == {}


def test_chain_round_trips_through_export(db):
    from app.routes.patch_panels import connect_ports, ConnectionCreate
    topo, a, b, cust_link, wan, front, back = _chain(db)
    connect_ports(topo.id, front["id"], ConnectionCreate(port=3, rear_port=7), db)
    update_port(topo.id, front["id"], 3, PortUpdate(status="cabled_used", node_id=a.id, link_id=wan.id), db)
    snap = json.loads(json.dumps(_serialize_topology(topo.id, db)))
    new = _import_topology_data(snap, db)
    panels = {p["name"]: p for p in list_panels(new.id, "DC1", db)}
    assert panels["FRONT"]["in_front_of_id"] == panels["BACK"]["id"]
    assert panels["FRONT"]["ports"]["3"]["rear_port"] == 7
    assert panels["FRONT"]["ports"]["3"]["node_name"] == "PE-A"
    assert panels["BACK"]["ports"]["7"]["link"]["far_node"] == "PE-C"


def test_connect_syncs_wan_link_and_moves_device_to_customer_side(db):
    """Connecting a customer-facing port to a wan-facing port mirrors the WAN
    link across the jumper and keeps the device on the customer-facing port."""
    from app.routes.patch_panels import connect_ports, ConnectionCreate
    topo, a, b, c, cust, cust_link, wan = _seed(db)
    front = _panel(db, topo, name="FRONT")
    back = _panel(db, topo, name="BACK")
    update_panel(topo.id, front["id"], PanelUpdate(in_front_of_id=back["id"]), force=False, db=db)
    # WAN link documented on the wan-facing port first, device on the front.
    update_port(topo.id, back["id"], 9, PortUpdate(status="cabled_reserved", link_id=wan.id), db)
    update_port(topo.id, front["id"], 3, PortUpdate(status="cabled_used", node_id=a.id, interface="Gi0/2",
                                                    label="C3", notes="XC-1"), db)
    out = connect_ports(topo.id, front["id"], ConnectionCreate(port=3, rear_port=9), db)
    assert out["moved_device_to"] is None
    fp = out["ports"]["3"]
    assert (fp["link_id"], fp["node_id"], fp["label"], fp["rear_port"]) == (wan.id, a.id, "C3", 9)

    # Legacy layout (device documented on the wan-facing port) moves forward.
    bp = db.query(PatchPanel).get(back["id"])
    ports = bp.ports
    ports["10"] = {"label": "", "status": "cabled_free", "node_id": b.id, "interface": "Gi0/0",
                   "link_id": None, "notes": ""}
    bp.ports = ports
    db.commit()
    out = connect_ports(topo.id, front["id"], ConnectionCreate(port=4, rear_port=10), db)
    assert out["moved_device_to"] == {"panel": "FRONT", "port": 4}
    assert (out["ports"]["4"]["node_id"], out["ports"]["4"]["status"]) == (b.id, "cabled_free")
    assert _ports(db, topo)["BACK"]["10"]["node_id"] is None   # still cabled, no device

    # Different links on the two sides, or a non-WAN link, refuse the jumper.
    update_port(topo.id, front["id"], 5, PortUpdate(status="cabled_used", link_id=cust_link.id), db)
    with pytest.raises(HTTPException):
        connect_ports(topo.id, front["id"], ConnectionCreate(port=5, rear_port=11), db)
    with pytest.raises(HTTPException):
        connect_ports(topo.id, front["id"], ConnectionCreate(port=5, rear_port=9), db)
