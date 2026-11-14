"""Lesson check evaluators against canned device output, and the runner with
device_exec stubbed."""
from types import SimpleNamespace

import pytest

from app.services import device_exec, lab_service, lesson_checks as lc

XR_ROUTES = """
Codes: C - connected, S - static, R - RIP, B - BGP, (>) - Diversion path
       O - OSPF, IA - OSPF inter area
       N1 - OSPF NSSA external type 1, N2 - OSPF NSSA external type 2

Gateway of last resort is 10.1.14.1 to network 0.0.0.0

O*IA 0.0.0.0/0 [110/2] via 10.1.14.1, 00:00:12, GigabitEthernet0/0/0/0
L    4.4.4.4/32 is directly connected, 00:10:01, Loopback0
O IA 5.5.5.5/32 [110/4] via 10.1.14.1, 00:03:11, GigabitEthernet0/0/0/0
O E2 203.0.113.0/24 [110/20] via 10.1.14.1, 00:01:02, GigabitEthernet0/0/0/0
O N2 198.51.100.0/24 [110/20] via 10.2.35.2, 00:00:40, GigabitEthernet0/0/0/1
O    3.3.3.3/32 [110/3] via 10.1.14.1, 00:03:11, GigabitEthernet0/0/0/0
"""
FRR_ROUTES = "O>* 10.2.5.0/24 [110/30] via 10.0.0.2, eth1, weight 1, 00:00:05\nO   10.1.4.0/24 [110/10] is directly connected, eth2\n"
XR_NEIGHBORS = """
Neighbors for OSPF 1

Neighbor ID     Pri   State           Dead Time   Address         Up Time    Interface
1.1.1.1         1     FULL/  -        00:00:38    10.0.12.1       00:01:05   Gi0/0/0/0
3.3.3.3         1     INIT/  -        00:00:31    10.0.23.2       00:00:02   Gi0/0/0/1
"""


def test_find_routes_codes():
    assert lc.find_routes(XR_ROUTES, "0.0.0.0/0", "O IA")             # "O*IA"
    assert lc.find_routes(XR_ROUTES, "5.5.5.5/32", "O IA")
    assert not lc.find_routes(XR_ROUTES, "5.5.5.5/32", "O E2")
    assert lc.find_routes(XR_ROUTES, "203.0.113.0/24", "O E2")
    assert lc.find_routes(XR_ROUTES, "198.51.100.0/24", "O N2")
    assert lc.find_routes(XR_ROUTES, "3.3.3.3/32", "O")
    assert not lc.find_routes(XR_ROUTES, "3.3.3.3/32", "O IA")
    assert lc.find_routes(XR_ROUTES, "4.4.4.4/32")                    # any protocol
    assert not lc.find_routes(XR_ROUTES, "9.9.9.9/32")
    assert lc.find_routes(FRR_ROUTES, "10.2.5.0/24", "O")             # "O>*"


XE_ROUTES = """
Gateway of last resort is 10.1.14.1 to network 0.0.0.0

O*IA  0.0.0.0/0 [110/101] via 10.1.14.1, 00:00:12, GigabitEthernet2
      3.0.0.0/32 is subnetted, 1 subnets
O        3.3.3.3 [110/3] via 10.0.13.2, 00:01:00, GigabitEthernet3
      10.0.0.0/8 is variably subnetted, 7 subnets, 4 masks
O IA     10.1.0.0/16 [110/201] via 10.0.12.2, 00:00:40, GigabitEthernet2
                     [110/201] via 10.0.13.1, 00:00:40, GigabitEthernet3
"""
XR_V6_ROUTES = """
L    2001:db8::1/128 is directly connected,
      00:10:01, Loopback0
O    2001:db8::3/128 
      [110/201] via fe80::5054:ff:fe12:3456, 00:00:21, GigabitEthernet0/0/0/0
O IA 2001:db8:1::/48 
      [110/101] via fe80::5054:ff:fe12:3457, 00:00:21, GigabitEthernet0/0/0/1
O E2 2001:db8:cafe::/48 
      [110/20] via fe80::5054:ff:fe12:3456, 00:00:21, GigabitEthernet0/0/0/0
"""
XE_V6_ROUTES = """
IPv6 Routing Table - default - 9 entries
Codes: C - Connected, L - Local, S - Static, U - Per-user Static route
       O - OSPF Intra, OI - OSPF Inter, OE1 - OSPF ext 1, OE2 - OSPF ext 2
       ON1 - OSPF NSSA ext 1, ON2 - OSPF NSSA ext 2
OI  ::/0 [110/101]
     via FE80::5054:FF:FE12:3456, GigabitEthernet2
LC  2001:DB8:1::4/128 [0/0]
     via Loopback0, receive
ON2 2001:DB8:BEEF::/48 [110/20]
     via FE80::5054:FF:FE12:3458, GigabitEthernet4
OE2 2001:DB8:CAFE::/48 [110/20], tag 0
     via FE80::5054:FF:FE12:3456, GigabitEthernet2
"""


def test_find_routes_xe_ipv6_metric_via():
    # IOS-XE classful headers / indented subnets, ECMP continuation lines
    assert lc.find_routes(XE_ROUTES, "0.0.0.0/0", "O IA", metric=101)
    assert lc.find_routes(XE_ROUTES, "3.3.3.3/32", "O", metric=3)     # mask from "3.0.0.0/32 is subnetted"
    assert not lc.find_routes(XE_ROUTES, "3.3.3.3/32", "O IA")
    assert lc.find_routes(XE_ROUTES, "10.1.0.0/16", "O IA", metric=201, via="10.0.13.1")
    assert not lc.find_routes(XE_ROUTES, "10.1.0.0/16", "O IA", metric=200)
    # IPv6: XR's entry spans two lines; XE's codes are OI / OE2 / ON2, hex in upper case
    assert lc.find_routes(XR_V6_ROUTES, "2001:db8::3/128", "O", metric=201, via="Gi")
    assert lc.find_routes(XR_V6_ROUTES, "2001:db8:1::/48", "O IA")
    assert lc.find_routes(XR_V6_ROUTES, "2001:db8:cafe::/48", "O E2")
    assert lc.find_routes(XE_V6_ROUTES, "::/0", "O IA")               # "OI"
    assert lc.find_routes(XE_V6_ROUTES, "2001:db8:beef::/48", "O N2", metric=20)
    assert lc.find_routes(XE_V6_ROUTES, "2001:DB8:CAFE:0::/48", "O E2", via="gigabitethernet2")
    assert not lc.find_routes(XE_V6_ROUTES, "2001:db8:cafe::/48", "O N2")
    assert lc.route_command("iosxe", ipv6=True) == "show ipv6 route"
    assert lc.route_command("iosxr", ipv6=True) == "show route ipv6"
    assert lc.validate_check({"type": "route", "device": "R1", "prefix": "nope"})
    assert lc.validate_check({"type": "route", "device": "R1", "prefix": "::/0", "metric": "x"})
    assert not lc.validate_check({"type": "route", "device": "R1", "prefix": "::/0", "metric": 5})


def test_eval_expect():
    ok, _ = lc.eval_expect(XR_NEIGHBORS, [{"regex": r"^1\.1\.1\.1\s+\d+\s+FULL"}])
    assert ok
    ok, why = lc.eval_expect(XR_NEIGHBORS, [{"regex": r"^3\.3\.3\.3\s+\d+\s+FULL"}])
    assert not ok and "expected to match" in why
    assert lc.eval_expect(XR_NEIGHBORS, [{"contains": "FULL"}, {"not_contains": "DOWN"}])[0]
    assert not lc.eval_expect(XR_NEIGHBORS, [{"contains": "FULL", "count": 2}])[0]
    assert lc.eval_expect("It is an Area Border Router", [{"regex": "area border router", "ignore_case": True}])[0]
    assert not lc.eval_expect("x INIT y", [{"not_regex": "INIT|EXSTART"}])[0]


def test_ping_parsing():
    assert device_exec.ping_success("Success rate is 80 percent (4/5), round-trip min/avg/max = 1/2/4 ms") == 80
    assert device_exec.ping_success("5 packets transmitted, 5 packets received, 0% packet loss") == 100
    assert device_exec.ping_success("3 packets transmitted, 0 received, 100% packet loss") == 0
    assert device_exec.ping_success("% Unknown host") is None
    assert device_exec.ping_command("iosxr", "5.5.5.5", 5, "4.4.4.4") == "ping 5.5.5.5 count 5 source 4.4.4.4"
    assert device_exec.ping_command("linux", "10.0.0.1", 3) == "ping -c 3 -W 1 10.0.0.1"


def test_validate_and_describe():
    assert lc.validate_check({"type": "ping", "from": "H1", "to": "1.1.1.1"}, {"H1"}) == []
    assert lc.validate_check({"type": "route", "device": "r4", "prefix": "1.0.0.0/8"}, {"R4"}) == []   # case-insensitive
    assert "needs 'prefix'" in " ".join(lc.validate_check({"type": "route", "device": "R4"}))
    assert lc.describe({"type": "route", "device": "R4", "prefix": "0.0.0.0/0", "protocol": "O IA"}) == \
        "R4: route O IA 0.0.0.0/0 present"
    assert lc.describe({"type": "cli", "device": "R1", "command": "x", "describe": "Custom"}) == "Custom"


def test_run_checks(monkeypatch, db):
    from app.models.node_instance import NodeInstance
    from app.models.topology import Topology
    t = Topology(name="t")
    db.add(t)
    db.flush()
    for i, (name, kind) in enumerate([("R4", "cisco_xrd"), ("R5", "cisco_xrd"), ("H1", "linux")]):
        db.add(NodeInstance(topology_id=t.id, name=name, kind=kind, image_ref="x", x=0, y=0))
    db.commit()
    ids = {n.name: n.id for n in db.query(NodeInstance).filter_by(topology_id=t.id)}
    monkeypatch.setattr(lab_service, "get_deployment_state", lambda db_, tid: {
        "nodes": {ids["R4"]: {"state": "running"}, ids["H1"]: {"state": "running"}, ids["R5"]: {"state": "exited"}}})
    monkeypatch.setattr(device_exec, "target_for", lambda db_, tid, nid: SimpleNamespace(
        name=next(k for k, v in ids.items() if v == nid), family="linux" if nid == ids["H1"] else "iosxr", image=""))

    sessions = []

    async def fake_run(t, cmds, timeout=60):
        sessions.append((t.name, list(cmds)))
        out = lambda c: ("5 packets transmitted, 5 received, 0% packet loss" if c.startswith("ping")  # noqa: E731
                         else XR_ROUTES if "route" in c else XR_NEIGHBORS)
        return [{"command": c, "output": out(c)} for c in cmds]
    monkeypatch.setattr(device_exec, "run_cli", fake_run)

    res = lc.run_checks(db, t.id, [
        {"type": "route", "device": "R4", "prefix": "0.0.0.0/0", "protocol": "O IA"},
        {"type": "route", "device": "R4", "prefix": "203.0.113.0/24", "protocol": "O E2", "present": False},
        {"type": "cli", "device": "r4", "command": "show ospf neighbor", "expect": [{"regex": r"^1\.1\.1\.1\s+\d+\s+FULL"}]},
        {"type": "ping", "from": "H1", "to": "10.2.5.10"},
        {"type": "route", "device": "R5", "prefix": "1.1.1.1/32"},
        {"type": "route", "device": "R9", "prefix": "1.1.1.1/32"},
    ])
    assert [r["ok"] for r in res] == [True, False, True, True, False, False]
    assert "still in the table" in res[1]["detail"]
    assert "isn't running" in res[4]["detail"]
    assert "No device named" in res[5]["detail"]
    assert lc.task_result(res)["passed"] is False
    # One session per device; the shared "show route ipv4" runs once.
    assert sorted(sessions) == [("H1", ["ping -c 5 -W 1 10.2.5.10"]), ("R4", ["show route ipv4", "show ospf neighbor"])]
    assert lc.task_result(res[:1])["passed"] is True
