"""Traffic generator nodes: config validation, YAML, API, import/export and the traffic check."""
import json

import pytest
import yaml

from app.services import lesson_checks as lc
from app.services import traffic_gen as tg
from app.services.clab_yaml import generate_clab_yaml
from tests.factories import make_link, make_node, make_topology

TRAFFIC = "plabs/traffic:latest"


def test_normalize_validates_and_fills_in():
    cfg = tg.normalize({
        "interface": {"ipv4": "10.1.3.10/24", "gateway4": "10.1.3.1"},
        "streams": [{"name": "VOICE", "dst": "10.1.4.10", "port": 5004, "rate_pps": 50, "dscp": "EF"},
                    {"name": "MC", "dst": "239.1.1.1", "dscp": "af41", "ttl": 16}],
        "receivers": [{"group": "232.1.1.1", "source": "10.1.3.10", "port": 5001}, {"port": 5004}],
        "reflector": {"enabled": True, "dscp": "cs1"}})
    assert cfg["interface"]["name"] == "eth1" and cfg["interface"]["ipv4"] == "10.1.3.10/24"
    assert [s["dscp"] for s in cfg["streams"]] == [46, 34] and cfg["streams"][1]["port"] == 5001
    assert cfg["receivers"][1] == {"group": "", "source": "", "port": 5004, "ipv6": False}
    assert cfg["reflector"] == {"enabled": True, "port": 7, "dscp": 8}
    assert tg.normalize({}) == tg.default_config()


@pytest.mark.parametrize("bad, msg", [
    ({"streams": [{"name": "A", "dst": "10.0.0.1"}, {"name": "A", "dst": "10.0.0.2"}]}, "two streams"),
    ({"streams": [{"name": "A", "dst": "nope"}]}, "destination"),
    ({"streams": [{"name": "A", "dst": "10.0.0.1", "dscp": "af99"}]}, "unknown DSCP"),
    ({"streams": [{"name": "A", "dst": "10.0.0.1", "rate_pps": 99999}]}, "rate"),
    ({"receivers": [{"group": "10.1.1.1"}]}, "isn't a multicast group"),
    ({"receivers": [{"group": "ff3e::1", "source": "10.1.3.10"}]}, "different IP versions"),
    ({"interface": {"gateway4": "10.1.3.1"}}, "needs a IPV4 address"),
    ({"interface": {"ipv4": "10.1.3.10"}}, "prefix length"),
])
def test_normalize_rejects(bad, msg):
    with pytest.raises(tg.TrafficConfigError, match=msg):
        tg.normalize(bad)


def test_yaml_mounts_the_config_and_edits_dont_change_it(db, tmp_path, monkeypatch):
    monkeypatch.setattr(tg, "LAB_STATE_DIR", tmp_path)
    t = make_topology(db, "TG")
    gen = make_node(db, t, name="TG1", kind="linux", image=TRAFFIC)
    host = make_node(db, t, name="H1", kind="linux", image="plabs/host:latest", startup_config="ip addr add 10.0.0.1/24 dev eth1")
    make_link(db, t, gen, host)
    assert gen.traffic == tg.default_config()
    spec = generate_clab_yaml(db, t.id)
    node = yaml.safe_load(spec["yaml"])["topology"]["nodes"]["TG1"]
    assert node["binds"] == ["traffic/TG1:/etc/plabs-traffic"] and "exec" not in node
    cfg_file = tmp_path / spec["lab_name"] / "traffic" / "TG1" / "config.json"
    assert json.loads(cfg_file.read_text())["streams"] == []
    gen.traffic = tg.normalize({"streams": [{"name": "S1", "dst": "10.0.0.1", "rate_pps": 5}]})
    db.commit()
    spec2 = generate_clab_yaml(db, t.id)
    assert spec2["yaml"] == spec["yaml"]                      # nothing to recreate
    assert json.loads(cfg_file.read_text())["streams"][0]["name"] == "S1"


def test_api_get_and_put(client, db, monkeypatch, tmp_path):
    monkeypatch.setattr(tg, "LAB_STATE_DIR", tmp_path)
    t = make_topology(db, "TG")
    gen = make_node(db, t, name="TG1", kind="linux", image=TRAFFIC)
    host = make_node(db, t, name="H1", kind="linux")
    db.commit()
    url = f"/api/topologies/{t.id}/nodes/{gen.id}/traffic"
    r = client.get(url).json()
    assert r["config"] == tg.default_config() and r["running"] is False and r["stats"] is None
    bad = client.put(url, json={"config": {"streams": [{"name": "X", "dst": "300.1.1.1"}]}})
    assert bad.status_code == 400 and "destination" in bad.json()["detail"]
    ok = client.put(url, json={"config": {"streams": [{"name": "VOICE", "dst": "10.1.4.10", "dscp": "ef"}]}})
    assert ok.status_code == 200 and ok.json()["config"]["streams"][0]["dscp"] == 46
    assert client.syncs == []                                # live: no reconcile
    assert client.get(f"/api/topologies/{t.id}/nodes/{host.id}/traffic").status_code == 400


def test_traffic_config_survives_export_and_import(db):
    from app.models.topology import Topology
    from app.services import topology_io
    t = make_topology(db, "TG")
    gen = make_node(db, t, name="TG1", kind="linux", image=TRAFFIC)
    gen.traffic = tg.normalize({"receivers": [{"group": "239.1.1.1"}]})
    db.commit()
    spec = topology_io.export_topology(db, t.id)
    assert spec["nodes"][0]["traffic"]["receivers"][0]["group"] == "239.1.1.1"
    t2 = Topology(name="copy")
    db.add(t2)
    db.commit()
    topology_io.build_into(db, t2, spec)
    db.commit()
    assert t2.nodes[0].traffic == gen.traffic


STATS = {
    "ts": 0,
    "streams": [{"name": "BULK", "dst": "10.1.4.10", "port": 5005, "dscp": 0, "pps": 75.0, "error": "",
                 "reflected": {"received": 900, "pps": 74.0, "loss_pct": 1.0, "dscp_back": {"10": 500, "12": 240},
                               "dscp_at_reflector": {"10": 600, "12": 300}, "rtt_ms": 3.1, "last_seen_s": 0.2}}],
    "flows": [
        {"src": "10.1.3.10", "dst": "239.1.1.1", "port": 5001, "stream": "MC1", "pps": 10.0, "loss_pct": 0.0,
         "dscp": {"0": 100}, "last_seen_s": 0.0},          # 0.0 = just now (not "missing")
        {"src": "10.1.3.10", "dst": "10.1.4.10", "port": 5004, "stream": "VOICE", "pps": 50.0, "loss_pct": 0.0,
         "dscp": {"46": 500}, "last_seen_s": 0.1},
        {"src": "10.1.3.10", "dst": "10.1.4.10", "port": 5005, "stream": "BULK", "pps": 40.0, "loss_pct": 37.5,
         "dscp": {"10": 280, "12": 120}, "last_seen_s": 0.1},
        {"src": "10.1.3.10", "dst": "232.1.1.1", "port": 5001, "stream": "OLD", "pps": 0, "loss_pct": None,
         "dscp": {}, "last_seen_s": 40},
    ],
}


@pytest.mark.parametrize("check, ok, why", [
    ({"flow": {"group": "239.1.1.1"}, "min_pps": 8}, True, ""),
    ({"flow": {"group": "239.1.1.1"}, "min_pps": 20}, False, "need at least 20"),
    ({"flow": {"stream": "VOICE"}, "dscp": "ef", "max_loss": 1}, True, ""),
    ({"flow": {"stream": "VOICE"}, "dscp": "af11"}, False, "arriving as ef 100%"),
    ({"flow": {"stream": "BULK"}, "dscp": ["af11", "af12"]}, True, ""),
    ({"flow": {"stream": "BULK"}, "max_loss": 10}, False, "37.5% loss"),
    ({"flow": {"group": "232.1.1.1"}, "present": False}, True, ""),          # stale flows don't count
    ({"flow": {"group": "232.1.1.1"}, "min_pps": 1}, False, "nothing matching"),
    ({"stream": "BULK", "reflected": True, "dscp": ["af11", "af12"], "dscp_at_reflector": ["af11", "af12"]}, True, ""),
    ({"stream": "BULK", "min_pps": 50}, True, ""),
    ({"stream": "NOPE"}, False, "no running stream"),
])
def test_eval_traffic(check, ok, why):
    got, detail, _out = lc.eval_traffic({"type": "traffic", "device": "R", **check}, json.dumps(STATS))
    assert got is ok and why in detail


def test_traffic_check_validation():
    assert lc.validate_check({"type": "traffic", "device": "RCV1", "flow": {"group": "239.1.1.1"}, "min_pps": 5}) == []
    assert lc.validate_check({"type": "traffic", "device": "RCV1"})
    assert any("DSCP" in e for e in lc.validate_check({"type": "traffic", "device": "R", "stream": "A", "dscp": "zz"}))
    assert any("unknown keys" in e for e in lc.validate_check({"type": "traffic", "device": "R", "stream": "A", "bogus": 1}))
    assert lc._command({"type": "traffic"}, None) == tg.STATS_COMMAND
    assert lc.eval_traffic({"type": "traffic", "stream": "A"}, "cat: no such file")[0] is False
