"""Actual link utilization: CSV import → per-link max-of-two-interfaces,
node/interface matching, inheritance, and the circuit report export."""
import csv
import io

from app.services.actual_utilization import (
    parse_metrics_csv, import_metrics, compute_actual_link_utilization, apply_link_delays,
    export_links_csv, canonical_iface, instance_candidates, EXPORT_HEADERS,
)
from tests.factories import make_topology, make_node, make_link


def _by_link(result):
    return {r["link_id"]: r for r in result["links"]}


def test_parse_csv_headers_and_values():
    rows, errors = parse_metrics_csv(
        "﻿Instance,Interface,peak_30d_bps,peak_90d_bps\n"
        "r1,Gi0/0/1,\"1,500,000\",2e6\n"
        "r1,Gi0/0/2,,\n"
        ",Gi0/0/3,1,1\n"
        "r1,Gi0/0/4,abc,1\n"
    )
    assert rows == [
        {"instance": "r1", "interface": "Gi0/0/1", "peak_30d_bps": 1.5e6, "peak_90d_bps": 2e6, "link_delay_us": None},
        {"instance": "r1", "interface": "Gi0/0/2", "peak_30d_bps": None, "peak_90d_bps": None, "link_delay_us": None},
    ]
    assert len(errors) == 2


def test_parse_csv_missing_column():
    rows, errors = parse_metrics_csv("instance,interface,peak_30d_bps\nr1,x,1\n")
    assert rows == [] and "peak_90d_bps" in errors[0]


def test_link_takes_higher_of_two_interfaces(db):
    t = make_topology(db)
    a, b = make_node(db, t, "r1"), make_node(db, t, "r2")
    l = make_link(db, t, a, b, bw=10, source_interface="Gi0/0/1", target_interface="Gi0/0/2")
    rows, _ = parse_metrics_csv(
        "instance,interface,peak_30d_bps,peak_90d_bps\n"
        "r1,Gi0/0/1,3000000000,4000000000\n"
        "r2,Gi0/0/2,5000000000,1000000000\n"
    )
    import_metrics(db, t.id, rows)
    r = _by_link(compute_actual_link_utilization(db, t.id))[l.id]
    assert r["peak_30d_bps"] == 5e9      # r2 side higher
    assert r["peak_90d_bps"] == 4e9      # r1 side higher
    assert r["source_matched"] and r["target_matched"]


def test_one_sided_and_missing_data(db):
    t = make_topology(db)
    a, b, c = make_node(db, t, "r1"), make_node(db, t, "r2"), make_node(db, t, "r3")
    l1 = make_link(db, t, a, b, source_interface="xe-0/0/0", target_interface="xe-0/0/1")
    l2 = make_link(db, t, b, c, source_interface="xe-0/0/2", target_interface="xe-0/0/3")
    import_metrics(db, t.id, [{"instance": "r1", "interface": "xe-0/0/0",
                               "peak_30d_bps": 1e6, "peak_90d_bps": 2e6}])
    res = _by_link(compute_actual_link_utilization(db, t.id))
    assert res[l1.id]["peak_30d_bps"] == 1e6 and res[l1.id]["has_data"]
    assert res[l2.id]["peak_30d_bps"] is None and not res[l2.id]["has_data"]


def test_matching_by_prometheus_id_port_fqdn_and_iface_abbrev(db):
    t = make_topology(db)
    a = make_node(db, t, "core1", prometheus_id="10.0.0.1")
    b = make_node(db, t, "core2")
    l = make_link(db, t, a, b, source_interface="GigabitEthernet0/0/0",
                  target_interface="Te0/1")
    import_metrics(db, t.id, [
        {"instance": "10.0.0.1:161", "interface": "Gi0/0/0", "peak_30d_bps": 7.0, "peak_90d_bps": 7.0},
        {"instance": "CORE2.example.net", "interface": "TenGigabitEthernet0/1", "peak_30d_bps": 9.0, "peak_90d_bps": 1.0},
        {"instance": "nowhere", "interface": "x", "peak_30d_bps": 1.0, "peak_90d_bps": 1.0},
        {"instance": "core1", "interface": "Gi9/9", "peak_30d_bps": 1.0, "peak_90d_bps": 1.0},
    ])
    res = compute_actual_link_utilization(db, t.id)
    r = _by_link(res)[l.id]
    assert r["peak_30d_bps"] == 9.0 and r["peak_90d_bps"] == 7.0
    assert res["unmatched_instances"] == ["nowhere"]
    assert res["unmatched_interfaces"] == [{"instance": "core1", "interface": "Gi9/9"}]


def test_canonical_and_instance_helpers():
    assert canonical_iface("GigabitEthernet0/0/1") == canonical_iface("gi0/0/1")
    assert canonical_iface("TenGigE0/0/0/1") == canonical_iface("Te0/0/0/1")
    assert canonical_iface("Ethernet1") == "et1"
    assert instance_candidates("r1.lab.net:9116") == ["r1.lab.net:9116", "r1.lab.net", "r1"]
    assert instance_candidates("10.1.1.1") == ["10.1.1.1"]


def test_reimport_replaces(db):
    t = make_topology(db)
    a, b = make_node(db, t, "r1"), make_node(db, t, "r2")
    l = make_link(db, t, a, b, source_interface="e1", target_interface="e2")
    import_metrics(db, t.id, [{"instance": "r1", "interface": "e1", "peak_30d_bps": 5.0, "peak_90d_bps": 5.0}])
    import_metrics(db, t.id, [{"instance": "r2", "interface": "e2", "peak_30d_bps": 1.0, "peak_90d_bps": 1.0}])
    r = _by_link(compute_actual_link_utilization(db, t.id))[l.id]
    assert r["peak_30d_bps"] == 1.0 and not r["source_matched"]


def test_child_sees_parent_metrics_and_overrides(db):
    p = make_topology(db, "P")
    a, b = make_node(db, p, "r1"), make_node(db, p, "r2")
    l = make_link(db, p, a, b, source_interface="e1", target_interface="e2")
    c = make_topology(db, "C", parent_id=p.id)
    import_metrics(db, p.id, [{"instance": "r1", "interface": "e1", "peak_30d_bps": 5.0, "peak_90d_bps": 5.0}])
    assert _by_link(compute_actual_link_utilization(db, c.id))[l.id]["peak_30d_bps"] == 5.0
    import_metrics(db, c.id, [{"instance": "r1", "interface": "e1", "peak_30d_bps": 2.0, "peak_90d_bps": 2.0}])
    assert _by_link(compute_actual_link_utilization(db, c.id))[l.id]["peak_30d_bps"] == 2.0
    assert _by_link(compute_actual_link_utilization(db, p.id))[l.id]["peak_30d_bps"] == 5.0



def test_child_override_matches_abbreviated_iface(db):
    p = make_topology(db, "P")
    a, b = make_node(db, p, "r1"), make_node(db, p, "r2")
    l = make_link(db, p, a, b, source_interface="GigabitEthernet0/0/1", target_interface="x")
    c = make_topology(db, "C", parent_id=p.id)
    import_metrics(db, p.id, [{"instance": "r1", "interface": "GigabitEthernet0/0/1", "peak_30d_bps": 5.0, "peak_90d_bps": 5.0}])
    import_metrics(db, c.id, [{"instance": "r1", "interface": "Gi0/0/1", "peak_30d_bps": 2.0, "peak_90d_bps": 2.0}])
    assert _by_link(compute_actual_link_utilization(db, c.id))[l.id]["peak_30d_bps"] == 2.0


def test_parse_csv_rejects_non_finite_and_negative():
    rows, errors = parse_metrics_csv(
        "instance,interface,peak_30d_bps,peak_90d_bps\n"
        "r1,e1,inf,1\n"
        "r1,e2,1,-5\n"
        "r1,e3,1,1\n"
    )
    assert [r["interface"] for r in rows] == ["e3"]
    assert len(errors) == 2

def test_export_csv(db):
    t = make_topology(db)
    a, b, c = make_node(db, t, "r1", location="NYC"), make_node(db, t, "r2"), make_node(db, t, "r3")
    make_link(db, t, a, b, bw=100, latency=12.5, circuit_id="CKT-1",
              source_interface="e1", target_interface="e2")
    make_link(db, t, b, c, bw=10, latency=3)
    import_metrics(db, t.id, [
        {"instance": "r1", "interface": "e1", "peak_30d_bps": 1234567.0, "peak_90d_bps": None},
        {"instance": "r2", "interface": "e2", "peak_30d_bps": 1000000.0, "peak_90d_bps": 2.5e9},
    ])
    rows = list(csv.reader(io.StringIO(export_links_csv(db, t.id))))
    assert tuple(rows[0]) == EXPORT_HEADERS
    assert rows[1] == ["CKT-1", "100", "r1", "r2", "1.23", "2500", "12.5", "measured"]
    assert rows[2] == ["", "10", "r2", "r3", "", "", "3", "measured"]


def test_export_combines_parallel_links(db):
    """Two links between r1/r2 (one in each direction): each link takes its
    higher interface, then the per-link peaks and speeds are summed."""
    t = make_topology(db)
    a, b = make_node(db, t, "r1"), make_node(db, t, "r2")
    make_link(db, t, a, b, bw=100, latency=5, circuit_id="CKT-A",
              source_interface="e1", target_interface="e1")
    make_link(db, t, b, a, bw=10, latency=8, circuit_id="CKT-B",
              source_interface="e2", target_interface="e2")
    import_metrics(db, t.id, [
        # link A: r1 side higher for 30d, r2 side higher for 90d
        {"instance": "r1", "interface": "e1", "peak_30d_bps": 4e9, "peak_90d_bps": 1e9},
        {"instance": "r2", "interface": "e1", "peak_30d_bps": 3e9, "peak_90d_bps": 6e9},
        # link B: only one side reported
        {"instance": "r2", "interface": "e2", "peak_30d_bps": 2e9, "peak_90d_bps": None},
    ])
    rows = list(csv.reader(io.StringIO(export_links_csv(db, t.id))))
    assert len(rows) == 2
    assert rows[1] == ["CKT-A; CKT-B", "110", "r1", "r2", "6000", "6000", "8", "measured"]


def test_api_import_get_export_delete(client, db):
    t = make_topology(db)
    a, b = make_node(db, t, "r1"), make_node(db, t, "r2")
    make_link(db, t, a, b, source_interface="e1", target_interface="e2", circuit_id="C9")
    db.commit()
    base = f"/api/topologies/{t.id}/actual-utilization"
    r = client.post(f"{base}/import-csv", json={
        "csv": "instance,interface,peak_30d_bps,peak_90d_bps\nr1,e1,1e9,2e9\nr2,e2,3e9,1e9\n"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["imported_rows"] == 2 and body["links_with_data"] == 1
    assert body["links"][0]["peak_30d_bps"] == 3e9 and body["links"][0]["peak_90d_bps"] == 2e9

    assert client.get(f"{base}/").json()["metric_rows"] == 2
    csv_text = client.get(f"{base}/export-csv").text
    assert csv_text.splitlines()[1] == "C9,10,r1,r2,3000,2000,1,measured"

    assert client.post(f"{base}/import-csv", json={"csv": "a,b\n1,2\n"}).status_code == 400
    assert client.delete(f"{base}/").json()["deleted"] == 2
    assert client.get(f"{base}/").json()["links_with_data"] == 0


def test_parse_csv_link_delay_column():
    rows, errors = parse_metrics_csv(
        "instance,interface,peak_30d_bps,peak_90d_bps,LINK_DELAY_US\n"
        "r1,e1,1,1,250\n"
        "r1,e2,1,1,\n"
        "r1,e3,1,1,-5\n"
    )
    assert [r["link_delay_us"] for r in rows] == [250.0, None]
    assert len(errors) == 1 and "link_delay_us" in errors[0]


def _delay_rows(*delays):
    hdr = "instance,interface,peak_30d_bps,peak_90d_bps,link_delay_us\n"
    return parse_metrics_csv(hdr + "".join(f"{i},{f},1,1,{d}\n" for i, f, d in delays))[0]


def test_link_delay_takes_higher_and_updates_latency(db):
    from app.models.link import Link
    t = make_topology(db)
    a, b = make_node(db, t, "r1"), make_node(db, t, "r2")
    l = make_link(db, t, a, b, source_interface="e1", target_interface="e2", latency=7)
    import_metrics(db, t.id, _delay_rows(("r1", "e1", 1500), ("r2", "e2", 2500)))
    res = compute_actual_link_utilization(db, t.id)
    assert _by_link(res)[l.id]["link_delay_us"] == 2500
    assert apply_link_delays(db, t.id, res["links"]) == {"updated": 1, "skipped_inherited": 0}
    assert db.get(Link, l.id).latency_ms == 2.5


def test_missing_or_zero_delay_keeps_existing_latency(db):
    from app.models.link import Link
    t = make_topology(db)
    a, b, c, d = (make_node(db, t, n) for n in ("r1", "r2", "r3", "r4"))
    l_zero = make_link(db, t, a, b, source_interface="e1", target_interface="e2", latency=4)
    l_blank = make_link(db, t, c, d, source_interface="e3", target_interface="e4", latency=6)
    import_metrics(db, t.id, _delay_rows(("r1", "e1", 0), ("r2", "e2", 0), ("r3", "e3", ""), ("r4", "e4", "")))
    res = compute_actual_link_utilization(db, t.id)
    assert apply_link_delays(db, t.id, res["links"])["updated"] == 0
    assert db.get(Link, l_zero.id).latency_ms == 4
    assert db.get(Link, l_blank.id).latency_ms == 6


def test_child_import_does_not_touch_inherited_link_latency(db):
    from app.models.link import Link
    p = make_topology(db, "p")
    a, b = make_node(db, p, "r1"), make_node(db, p, "r2")
    l = make_link(db, p, a, b, source_interface="e1", target_interface="e2", latency=3)
    c = make_topology(db, "c", parent_id=p.id)
    import_metrics(db, c.id, _delay_rows(("r1", "e1", 9000)))
    res = compute_actual_link_utilization(db, c.id)
    assert apply_link_delays(db, c.id, res["links"]) == {"updated": 0, "skipped_inherited": 1}
    assert db.get(Link, l.id).latency_ms == 3


def test_api_import_applies_link_delay(client, db):
    from app.models.link import Link
    t = make_topology(db)
    a, b = make_node(db, t, "r1"), make_node(db, t, "r2")
    l = make_link(db, t, a, b, source_interface="e1", target_interface="e2")
    db.commit()
    r = client.post(f"/api/topologies/{t.id}/actual-utilization/import-csv", json={
        "csv": "instance,interface,peak_30d_bps,peak_90d_bps,link_delay_us\nr1,e1,1,1,800\nr2,e2,1,1,1200\n"})
    assert r.status_code == 200, r.text
    assert r.json()["latency_updated_links"] == 1
    db.expire_all()
    assert db.get(Link, l.id).latency_ms == 1.2
