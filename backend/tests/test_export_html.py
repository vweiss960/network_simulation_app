"""Offline HTML export: merged (inheritance-aware) data + enrichment.

The export's HTML branch builds from the merged topology so a CHILD topology
includes inherited parent links (the bug: it used to serialize only the child's
own rows). It also embeds per-link utilization + per-demand contributions,
demand paths, switchable views, and persisted per-flow Monte Carlo reports.
"""
import json

from app.routes.topologies import _html_export_data
from tests.factories import make_topology, make_node, make_link, make_flow


def _seed_parent_child(db):
    """Parent A-B-C (with a planned C-D-ish link) + child adding node E and a
    planning link, plus a flow with a persisted availability report."""
    parent = make_topology(db, "parent")
    a = make_node(db, parent, "A")
    b = make_node(db, parent, "B")
    c = make_node(db, parent, "C")
    make_link(db, parent, a, b, bw=100.0)
    make_link(db, parent, b, c, bw=100.0)
    pd = make_node(db, parent, "D", planned=True)
    make_link(db, parent, c, pd, bw=100.0, planned=True)  # planned link
    f = make_flow(db, parent, "F1", a, c, bw=40.0)
    f.availability_report = {
        "availability": 0.99999, "downtime_min_yr": 5.2, "downtime_std_min_yr": 1.1,
        "disjoint_paths": 1, "iterations": 1000,
        "downtime_percentiles_min": {"p50": 1, "p90": 5, "p99": 20},
        "downtime_histogram_min": [{"bin_start": 0, "bin_end": 1, "count": 900},
                                   {"bin_start": 1, "bin_end": 2, "count": 100}],
    }
    child = make_topology(db, "child", parent_id=parent.id)
    e = make_node(db, child, "E")
    make_link(db, child, c, e, bw=50.0)  # child-local link C-E
    db.commit()
    return parent, child


def test_child_export_includes_inherited_links(db):
    parent, child = _seed_parent_child(db)
    data = _html_export_data(child.id, db, view_ids=[], include_planned=True,
                             show_paths=True, include_reports=True)
    pairs = {(l["source_node"], l["target_node"]) for l in data["links"]}
    norm = {frozenset(p) for p in pairs}
    # Inherited parent links AND the child-local link are all present.
    assert frozenset({"A", "B"}) in norm
    assert frozenset({"B", "C"}) in norm
    assert frozenset({"C", "E"}) in norm
    node_names = {n["name"] for n in data["nodes"]}
    assert {"A", "B", "C", "E"} <= node_names


def test_export_enrichment_blocks_present(db):
    parent, child = _seed_parent_child(db)
    data = _html_export_data(child.id, db, view_ids=[], include_planned=True,
                             show_paths=True, include_reports=True)
    # The export carries exactly one flattened view (Default + selected views).
    assert len(data["_export_views"]) == 1
    combined = data["_export_views"][0]["node_names"]
    assert "A" in combined and "E" in combined  # inherited + child-local nodes
    # Demand paths embedded with node-name sequences.
    assert any(fp["flow_name"] == "F1" and fp["paths_named"] for fp in data["_flow_paths"])
    # Some link carries utilization + per-demand contributions.
    utils = [l["_util"] for l in data["links"] if l.get("_util")]
    assert utils, "expected at least one link with utilization"
    assert any("demand_contributions" in u for u in utils)
    # Persisted Monte Carlo report rides along on the flow.
    f1 = next(f for f in data["flows"] if f["name"] == "F1")
    assert f1["availability_report"]["downtime_histogram_min"]


def _child_view(db, child, node_names, name="V", color=""):
    from app.models.topology_view import TopologyView
    from app.models.node_instance import NodeInstance
    import json as _json
    ids = [n.id for n in db.query(NodeInstance).filter_by(topology_id=child.id).all()
           if n.name in node_names]
    v = TopologyView(topology_id=child.id, name=name, color=color, node_ids_json=_json.dumps(ids))
    db.add(v); db.commit()
    return v


def _node(data, name):
    return next(n for n in data["nodes"] if n["name"] == name)


def test_selected_view_colors_nodes_with_stored_color(db):
    parent, child = _seed_parent_child(db)
    view = _child_view(db, child, {"E"}, name="Blue", color="#2266ff")
    data = _html_export_data(child.id, db, view_ids=[view.id], include_planned=True,
                             show_paths=True, include_reports=True)
    assert _node(data, "E").get("_view_color") == "#2266ff"
    assert {"name": "Blue", "color": "#2266ff"} in data["_view_legend"]


def test_view_without_stored_color_gets_palette_color(db):
    # This is the real-world case: views usually have no stored color; the app
    # shows them in a palette color, and the export must match.
    parent, child = _seed_parent_child(db)
    view = _child_view(db, child, {"E"}, name="NoColor", color="")
    data = _html_export_data(child.id, db, view_ids=[view.id], include_planned=True,
                             show_paths=True, include_reports=True)
    assert _node(data, "E").get("_view_color") == "#22d3ee"  # first palette color
    assert {"name": "NoColor", "color": "#22d3ee"} in data["_view_legend"]


def test_only_view_members_are_node_colored(db):
    # Only the view's members are node-colored (keyed by id, so duplicate names
    # can't bleed onto unrelated nodes).
    parent, child = _seed_parent_child(db)
    view = _child_view(db, child, {"E"}, name="Blue", color="#2266ff")
    data = _html_export_data(child.id, db, view_ids=[view.id], include_planned=True,
                             show_paths=True, include_reports=True)
    colored = {n["name"] for n in data["nodes"] if n.get("_view_color")}
    assert colored == {"E"}


def test_view_referencing_shadowed_node_id_does_not_color_visible_node(db):
    # Matches the app: coloring is by the rendered node's own id vs the view's
    # raw node_ids (no shadow->winner remap). A child view that references a
    # shadowed local node id must NOT color the visible inherited (parent) node.
    from app.models.topology import Topology
    from app.models.node_instance import NodeInstance
    from app.models.topology_view import TopologyView
    import json as _json
    parent = make_topology(db, "p")
    make_node(db, parent, "A")
    child = make_topology(db, "c", parent_id=parent.id)
    ca = make_node(db, child, "A")  # local shadow of parent A (parent wins)
    make_node(db, child, "Z")
    view = TopologyView(topology_id=child.id, name="V", color="#f59e0b",
                        node_ids_json=_json.dumps([ca.id]))  # references the shadowed id
    db.add(view); db.commit()
    data = _html_export_data(child.id, db, view_ids=[view.id], include_planned=True,
                             show_paths=True, include_reports=True)
    assert not any(n.get("_view_color") for n in data["nodes"] if n["name"] == "A")


def test_link_colored_when_touching_view_not_for_default_links(db):
    # Matches the app: a link takes the view color when EITHER endpoint is in the
    # view; links with neither endpoint in the view (default links) stay uncolored.
    parent, child = _seed_parent_child(db)
    view = _child_view(db, child, {"E"}, name="Blue", color="#2266ff")
    data = _html_export_data(child.id, db, view_ids=[view.id], include_planned=True,
                             show_paths=True, include_reports=True)
    by_pair = {frozenset((l["source_node"], l["target_node"])): l for l in data["links"]}
    assert by_pair[frozenset(("C", "E"))].get("_view_color") == "#2266ff"   # touches E
    assert "_view_color" not in by_pair[frozenset(("A", "B"))]              # default link
    assert "_view_color" not in by_pair[frozenset(("B", "C"))]             # default link


def test_planned_link_included_and_utilized(db):
    parent, child = _seed_parent_child(db)
    data = _html_export_data(parent.id, db, view_ids=[], include_planned=True,
                             show_paths=True, include_reports=True)
    planned = [l for l in data["links"] if l["is_planned"]]
    assert planned, "planned link must be present in the export"


def test_planned_split_local_vs_inherited(db):
    """include_planned covers the exported topology's OWN planned items;
    include_planned_parent covers planned items inherited from the parent
    chain. Links touching a dropped planned node disappear with it."""
    from app.models.node_instance import NodeInstance
    parent, child = _seed_parent_child(db)
    c = db.query(NodeInstance).filter_by(topology_id=parent.id, name="C").first()
    cp = make_node(db, child, "CP", planned=True)  # child-local planned
    make_link(db, child, c, cp, bw=10.0, planned=True)
    db.commit()

    def export(local, inherited):
        d = _html_export_data(child.id, db, view_ids=[], include_planned=local,
                              show_paths=False, include_reports=False,
                              include_spof=False, include_planned_parent=inherited)
        nodes = {n["name"] for n in d["nodes"]}
        links = {(l["source_node"], l["target_node"]) for l in d["links"]}
        return nodes, links

    nodes, links = export(True, True)
    assert {"A", "B", "C", "D", "E", "CP"} <= nodes  # D = parent planned

    nodes, links = export(False, False)
    assert "D" not in nodes and "CP" not in nodes and {"A", "B", "C", "E"} <= nodes
    assert ("C", "D") not in links and ("C", "CP") not in links

    nodes, links = export(True, False)   # child planned only
    assert "CP" in nodes and ("C", "CP") in links
    assert "D" not in nodes and ("C", "D") not in links

    nodes, links = export(False, True)   # inherited planned only
    assert "D" in nodes and ("C", "D") in links
    assert "CP" not in nodes and ("C", "CP") not in links


def test_spof_opt_out_omits_spof_block(db):
    parent, child = _seed_parent_child(db)
    with_spof = _html_export_data(child.id, db, view_ids=[], include_planned=True,
                                  show_paths=True, include_reports=True, include_spof=True)
    without = _html_export_data(child.id, db, view_ids=[], include_planned=True,
                                show_paths=True, include_reports=True, include_spof=False)
    assert "_spof" in with_spof and "_spof" not in without


def test_include_reports_toggle_strips_report(db):
    parent, child = _seed_parent_child(db)
    data = _html_export_data(parent.id, db, view_ids=[], include_planned=True,
                             show_paths=True, include_reports=False)
    f1 = next(f for f in data["flows"] if f["name"] == "F1")
    assert "availability_report" not in f1


def test_json_export_still_works_for_child(client, db):
    parent, child = _seed_parent_child(db)
    r = client.get(f"/api/topologies/{child.id}/export?format=json")
    assert r.status_code == 200
    payload = json.loads(r.content)
    # Raw json export keeps the child's OWN rows only (unchanged behavior) and
    # now carries additive ids.
    assert payload["nodes"] and "id" in payload["nodes"][0]


def test_include_default_off_limits_to_selected_view(db):
    # With the Default view off, the combined view = only the selected saved
    # view's nodes (so the export shows just that view).
    parent, child = _seed_parent_child(db)
    view = _child_view(db, child, {"E"}, name="OnlyE", color="#2266ff")
    on = _html_export_data(child.id, db, view_ids=[view.id], include_planned=True,
                           show_paths=True, include_reports=True, include_default=True)
    off = _html_export_data(child.id, db, view_ids=[view.id], include_planned=True,
                            show_paths=True, include_reports=True, include_default=False)
    assert "A" in on["_export_views"][0]["node_names"]       # default nodes present
    assert off["_export_views"][0]["node_names"] == ["E"]    # only the selected view


def _seed_customers(db):
    """X-A-B-C topology, X a customer node owned by Acme. F1 (X->C) belongs to
    Acme via its source node; F2 (A->C) touches no customer node."""
    from app.models.customer import Customer
    t = make_topology(db, "custT")
    acme = Customer(topology_id=t.id, name="Acme")
    db.add(acme)
    db.flush()
    x = make_node(db, t, "X", role="customer", customer_id=acme.id)
    a = make_node(db, t, "A")
    b = make_node(db, t, "B")
    c = make_node(db, t, "C")
    make_link(db, t, x, a, bw=100.0)
    make_link(db, t, a, b, bw=100.0)
    make_link(db, t, b, c, bw=100.0)
    make_flow(db, t, "F1", x, c, bw=10.0)
    make_flow(db, t, "F2", a, c, bw=20.0)  # no customer
    db.commit()
    return t, acme


def _util_map(data):
    return {l["id"]: l["_util"]["used_gbps"] for l in data["links"] if l.get("_util")}


def test_customer_filter_scopes_paths_not_utilization(db):
    t, acme = _seed_customers(db)
    full = _html_export_data(t.id, db, view_ids=[], include_planned=True,
                             show_paths=True, include_reports=False,
                             include_customer_nodes=True)  # no filter (customer_ids=None)
    acme_only = _html_export_data(t.id, db, view_ids=[], include_planned=True,
                                  show_paths=True, include_reports=False, include_customer_nodes=True,
                                  customer_ids=[acme.id], include_unassigned=False)
    assert {fp["flow_name"] for fp in full["_flow_paths"]} == {"F1", "F2"}
    assert {fp["flow_name"] for fp in acme_only["_flow_paths"]} == {"F1"}   # only Acme's paths
    # Utilization reflects ALL flows regardless of the path filter — identical.
    assert _util_map(full) == _util_map(acme_only)
    assert any(fp["customer_name"] == "Acme" for fp in acme_only["_flow_paths"])


def test_customer_filter_unassigned_only(db):
    t, acme = _seed_customers(db)
    d = _html_export_data(t.id, db, view_ids=[], include_planned=True, show_paths=True,
                          include_reports=False, customer_ids=[], include_unassigned=True,
                          include_customer_nodes=True)
    assert {fp["flow_name"] for fp in d["_flow_paths"]} == {"F2"}  # only the unassigned flow


def test_html_endpoint_returns_self_contained_page(client, db):
    parent, child = _seed_parent_child(db)
    r = client.get(f"/api/topologies/{child.id}/export?format=html&include_planned=1&show_paths=1&include_reports=1")
    assert r.status_code == 200
    body = r.text
    assert "<!DOCTYPE html>" in body and 'id="topo-data"' in body
    assert "applyViewFilter" in body and "highlightFlow" in body


def test_theme_param_sets_starting_theme(client, db):
    parent, child = _seed_parent_child(db)
    dark = client.get(f"/api/topologies/{parent.id}/export?format=html")
    light = client.get(f"/api/topologies/{parent.id}/export?format=html&theme=light")
    assert dark.status_code == 200 and light.status_code == 200
    # Default = dark: no light class on body; light start flips the class.
    assert '<body>' in dark.text and 'class="light"' not in dark.text
    assert '<body class="light">' in light.text
    # Both ship BOTH palettes + the in-page toggle.
    for body in (dark.text, light.text):
        assert 'body.light{' in body and 'const THEMES' in body
        assert 'id="theme-btn"' in body and 'applyTheme' in body
    # Starting THEMES selection matches the param.
    assert 'THEMES["dark"]' in dark.text and 'THEMES["light"]' in light.text
    # Link coloring is a dropdown: Utilization / Plain (gray) / View color.
    # Plain is the runtime default in BOTH themes; the others overlay colors.
    for body in (dark.text, light.text):
        assert 'value="plain"' in body and 'plainLink' in body
        assert 'value="util"' in body
        assert 'linkColorSelect.value = "plain"' in body
    # Invalid theme is rejected.
    assert client.get(f"/api/topologies/{parent.id}/export?format=html&theme=neon").status_code == 422


def test_paths_off_strips_per_link_demand_attribution(db):
    """show_paths=False removes the flow->link mapping (Demands on this link /
    node Flows section data): _util keeps only the numeric utilization."""
    parent, child = _seed_parent_child(db)

    def utils(show):
        d = _html_export_data(parent.id, db, view_ids=[], include_planned=True,
                              show_paths=show, include_reports=False, include_spof=False)
        return d, [l["_util"] for l in d["links"] if l.get("_util")]

    d, with_paths = utils(True)
    assert "_flow_paths" in d
    assert any(u.get("flow_names") or u.get("demand_contributions") for u in with_paths)

    d, without = utils(False)
    assert "_flow_paths" not in d
    assert without, "utilization itself must survive"
    for u in without:
        assert "flow_names" not in u and "demand_contributions" not in u
        assert "utilization_pct" in u


def test_reports_button_only_when_reports_present(client, db):
    parent, _child = _seed_parent_child(db)  # seed flow HAS a stored MC report
    with_reports = client.get(f"/api/topologies/{parent.id}/export?format=html&include_reports=1")
    assert 'id="mc-btn"' in with_reports.text
    without = client.get(f"/api/topologies/{parent.id}/export?format=html&include_reports=0")
    assert 'id="mc-btn"' not in without.text


def test_cui_banner_and_redaction(client, db):
    parent, _child = _seed_parent_child(db)
    from app.models.node_instance import NodeInstance
    a = db.query(NodeInstance).filter_by(topology_id=parent.id, name="A").first()
    a.vendor, a.model, a.platform, a.mgmt_ip = "cisco", "8201", "iosxr", "10.0.0.1"
    a.interfaces_json = json.dumps([{"name": "HundredGigE0/0/0/0", "speed_gbps": 100}])
    db.commit()

    plain = client.get(f"/api/topologies/{parent.id}/export?format=html")
    assert 'id="cui-banner"' not in plain.text

    cui = client.get(f"/api/topologies/{parent.id}/export?format=html&cui=1")
    assert '<div id="cui-banner">CUI</div>' in cui.text
    assert "10.0.0.1" in cui.text  # marked but not redacted

    redacted = client.get(
        f"/api/topologies/{parent.id}/export?format=html&cui=1"
        "&strip_device_info=1&strip_interface_info=1")
    body = redacted.text
    assert '<div id="cui-banner">CUI</div>' in body
    for leaked in ("cisco", "8201", "iosxr", "10.0.0.1", "HundredGigE0/0/0/0"):
        assert leaked not in body, f"redacted value {leaked!r} leaked into the export"


def test_every_node_export_field_is_classified_for_cui_redaction(db):
    """Guard: the roundtrip-coverage test forces every new NodeInstance column
    into _node_export_dict, but nothing forces it into the CUI redaction
    denylist — so every serializer key must be explicitly classified here as
    kept or stripped. A new key failing this test means: decide whether
    'Remove device/interface information' must blank it, update the strip
    block in _html_export_data, then classify it below."""
    parent, _child = _seed_parent_child(db)
    d = _html_export_data(parent.id, db, view_ids=[], include_planned=True,
                          show_paths=False, include_reports=False, include_spof=False)
    stripped_device = {"template_name", "vendor", "model", "platform", "mgmt_ip",
                       "prometheus_id", "custom_attrs", "custom_iface_refs",
                       "config_template_id", "config_template_group_id"}
    stripped_iface = {"interfaces", "custom_iface_refs"}
    kept = {"id", "name", "x", "y", "latitude", "longitude", "geo_latitude",
            "geo_longitude", "location", "mtbf_hours", "mttr_hours",
            "capacity_gbps", "is_planned", "role", "customer_name", "color",
            "is_route_reflector", "is_super_route_reflector", "rr_peer_names"}
    unclassified = set(d["nodes"][0].keys()) - stripped_device - stripped_iface - kept
    assert not unclassified, f"classify new node export fields for CUI redaction: {unclassified}"


def test_customer_nodes_excluded_by_default(db):
    from app.models.node_instance import NodeInstance
    parent, child = _seed_parent_child(db)
    a = db.query(NodeInstance).filter_by(topology_id=parent.id, name="A").first()
    b = db.query(NodeInstance).filter_by(topology_id=parent.id, name="B").first()
    cust = make_node(db, parent, "Cust-1", role="customer")
    make_link(db, parent, b, cust, bw=10.0)
    make_flow(db, parent, "F-cust", b, cust, bw=2.0)
    # Transit case: this flow rides the kept backbone link A-B on its way to
    # the excluded customer node — its name must not leak into A-B's _util.
    make_flow(db, parent, "F-transit", a, cust, bw=3.0)
    db.commit()

    def export(include):
        d = _html_export_data(child.id, db, view_ids=[], include_planned=True,
                              show_paths=True, include_reports=False,
                              include_spof=True, include_customer_nodes=include)
        return d

    d = export(False)
    assert "Cust-1" not in {n["name"] for n in d["nodes"]}
    assert not any("Cust-1" in (l["source_node"], l["target_node"]) for l in d["links"])
    assert {"F-cust", "F-transit"}.isdisjoint({f["name"] for f in d["flows"]})
    assert all("Cust-1" not in p for fp in d.get("_flow_paths", []) for p in fp["paths_named"])
    assert all(s["name"] != "Cust-1" for s in d.get("_spof", []))
    # Excluded flows must not be named (or leak as raw ids) in kept links'
    # demand attribution; utilization totals still count their traffic.
    for l in d["links"]:
        u = l.get("_util") or {}
        names = set(u.get("flow_names", [])) | {c["flow_name"] for c in u.get("demand_contributions", [])}
        assert {"F-cust", "F-transit"}.isdisjoint(names)
        assert not any(n.isdigit() for n in names), f"raw flow id leaked: {names}"

    d = export(True)
    assert "Cust-1" in {n["name"] for n in d["nodes"]}
    assert "F-cust" in {f["name"] for f in d["flows"]}


def test_title_param_names_viewer_and_download(client, db):
    parent, _child = _seed_parent_child(db)
    # Default title: topology name with spaces as underscores, in <h1>, <title>,
    # and the download filename.
    default = client.get(f"/api/topologies/{parent.id}/export?format=html")
    expected = parent.name.replace(" ", "_")
    assert f"<h1>{expected}</h1>" in default.text
    assert f"<title>{expected} — Network Topology</title>" in default.text
    assert f'filename="{expected}.html"' in default.headers["content-disposition"]
    # Custom title flows through (HTML-escaped in the page, quotes stripped
    # from the filename header).
    custom = client.get(f"/api/topologies/{parent.id}/export?format=html&title=Backbone+%22Q3%22+%3Cplan%3E")
    assert "<h1>Backbone &quot;Q3&quot; &lt;plan&gt;</h1>" in custom.text
    assert "<plan>" not in custom.text
    assert 'filename="Backbone Q3 <plan>.html"' in custom.headers["content-disposition"]
    # The title uses the app's PHANERO wordmark styling via the --title var.
    assert "--title:#f1f5f9" in default.text and "--title:#0f172a" in default.text
    assert "letter-spacing:0.2em" in default.text
    # Non-latin-1 titles must not 500 on Content-Disposition encoding: the
    # page keeps the full title, the filename keeps only latin-1 chars.
    cjk = client.get(f"/api/topologies/{parent.id}/export?format=html&title=%E6%8B%93%E6%89%91%20plan%20%E2%80%94%20Q3")
    assert cjk.status_code == 200
    assert "<h1>拓扑 plan — Q3</h1>" in cjk.text
    assert 'filename=" plan  Q3.html"' in cjk.headers["content-disposition"]
