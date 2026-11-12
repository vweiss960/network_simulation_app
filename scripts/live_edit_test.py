"""Live persistence test: do links + live configs survive topology edits?

Builds a throwaway lab (XRd R1/R2, FRR F1/F2), configures interfaces LIVE
(not in startup configs), then adds/removes nodes and links on the running
lab and checks after each step:
  * which containers were recreated (container id changed),
  * every device still has its live config,
  * every configured link still passes traffic (ping).
Usage: python3 scripts/live_edit_test.py <jwt> [--keep] [--no-vm]
(needs the XRd CP, FRR and Catalyst 8000v templates to have images; ~20 min with the VM)
"""
import json
import subprocess
import sys
import time

import requests

A = "http://127.0.0.1:8100/api"
TOK = sys.argv[1]
S = requests.Session()
S.headers["Authorization"] = f"Bearer {TOK}"
T0 = time.time()


def log(*a):
    print(f"[{time.time() - T0:6.0f}s]", *a, flush=True)


def call(method, path, **kw):
    r = S.request(method, A + path, timeout=kw.pop("timeout", 600), **kw)
    if r.status_code >= 400:
        raise RuntimeError(f"{method} {path}: {r.status_code} {r.text[:400]}")
    return r.json() if r.text else None


TPL = {t["name"]: t["id"] for t in call("GET", "/node-templates/")}
XRD, FRR, C8K = TPL["Cisco XRd Control Plane"], TPL["FRR Router"], TPL["Cisco Catalyst 8000v"]
WITH_VM = "--no-vm" not in sys.argv

tid = call("POST", "/topologies/", json={"name": "persist-test"})["id"]
lab_name = None
N = {}


def node(name, tpl, x):
    n = call("POST", f"/topologies/{tid}/nodes/", json={"template_id": tpl, "x": x, "y": 100})
    if n["name"] != name:
        call("PATCH", f"/topologies/{tid}/nodes/{n['id']}", json={"name": name})
    N[name] = n["id"]
    return n["id"]


def link(a, ai, b, bi):
    return call("POST", f"/topologies/{tid}/links/", json={
        "source_node_id": N[a], "target_node_id": N[b], "source_interface": ai, "target_interface": bi})["id"]


def wait_synced(timeout=900):
    time.sleep(2)
    t = time.time()
    while time.time() - t < timeout:
        st = call("GET", f"/topologies/{tid}/lab/status")
        if st.get("status") == "running" and not st.get("pending_sync"):
            return st
        if st.get("status") == "failed":
            raise RuntimeError(f"lab failed: {st.get('last_error')}")
        time.sleep(3)
    raise RuntimeError("sync timeout")


def cids():
    out = subprocess.run(["docker", "ps", "-a", "--filter", f"label=containerlab={lab_name}",
                          "--format", "{{.Names}} {{.ID}}"], capture_output=True, text=True).stdout
    return {l.split()[0].rsplit("-", 1)[-1]: l.split()[1] for l in out.splitlines()}


def cli(name, cmds, timeout=60):
    return call("POST", f"/topologies/{tid}/nodes/{N[name]}/cli", json={"commands": cmds, "timeout": timeout})


def wait_cli(name, timeout=1200):
    t = time.time()
    while time.time() - t < timeout:
        try:
            cli(name, ["show version"] if name.startswith("R") else ["show version"], timeout=20)
            return
        except Exception:
            time.sleep(5)
    raise RuntimeError(f"{name} CLI never came up")


def running(name):
    return call("GET", f"/topologies/{tid}/nodes/{N[name]}/running-config")["config"]


def configure(name, cfg):
    return call("POST", f"/topologies/{tid}/nodes/{N[name]}/configure", json={"config": cfg})


EXPECT = {}   # node -> list of substrings that must be in its running config
PINGS = []    # (node, ip)


def ping_ok(name, ip):
    if name.startswith("R"):
        out = json.dumps(cli(name, [f"ping {ip} count 3"]))
        return "!!" in out
    if name.startswith("C"):
        out = json.dumps(cli(name, [f"ping {ip} repeat 3"]))
        return "!!" in out
    rc = subprocess.run(["docker", "exec", f"clab-{lab_name}-{name}", "ping", "-c2", "-W1", ip],
                        capture_output=True).returncode
    return rc == 0


NO_PINGS = []   # (node, ip) that must FAIL (unplugged cables)


def check(step, before, allow_recreate=()):
    after = cids()
    recreated = sorted(k for k in after if k in before and before[k] != after[k])
    added = sorted(k for k in after if k not in before)
    log(f"--- {step}: recreated={recreated} added={added}")
    unexpected = [r for r in recreated if r not in allow_recreate]
    for name in recreated + added:
        if name in N:
            wait_cli(name)
    fails = [f"{r} restarted" for r in unexpected]
    for name, subs in EXPECT.items():
        if name not in N:
            continue
        try:
            cfg = running(name)
        except Exception as e:
            fails.append(f"{name}: running-config failed {e}")
            continue
        for s in subs:
            if s not in cfg:
                fails.append(f"{name}: lost config {s!r}")
    for name, ip in PINGS:
        ok = False
        for _ in range(6):            # XR adjacency / ARP can take a moment
            if ping_ok(name, ip):
                ok = True
                break
            time.sleep(5)
        if not ok:
            fails.append(f"{name} -> {ip}: ping failed")
    for name, ip in NO_PINGS:
        if ping_ok(name, ip):
            fails.append(f"{name} -> {ip}: still reachable after unplug")
    log("    PASS" if not fails else "    FAIL: " + "; ".join(fails))
    RESULTS.append((step, recreated, added, fails))
    return after


RESULTS = []
try:
    node("R1", XRD, 100); node("R2", XRD, 300); node("F1", FRR, 500); node("F2", FRR, 700)
    link("R1", "GigabitEthernet0/0/0/0", "R2", "GigabitEthernet0/0/0/0")
    link("F1", "eth1", "F2", "eth1")
    log("deploying")
    lab_name = call("POST", f"/topologies/{tid}/lab/deploy", timeout=1800)["lab_name"]
    for n in ("R1", "R2", "F1", "F2"):
        wait_cli(n)
    log("configuring live")
    configure("R1", "interface GigabitEthernet0/0/0/0\n ipv4 address 10.0.12.1 255.255.255.0\n no shutdown\n")
    configure("R2", "interface GigabitEthernet0/0/0/0\n ipv4 address 10.0.12.2 255.255.255.0\n no shutdown\n")
    configure("F1", "interface eth1\n ip address 10.0.34.1/24\n")
    configure("F2", "interface eth1\n ip address 10.0.34.2/24\n")
    EXPECT.update({"R1": ["10.0.12.1"], "R2": ["10.0.12.2"], "F1": ["10.0.34.1"], "F2": ["10.0.34.2"]})
    PINGS.extend([("R1", "10.0.12.2"), ("F1", "10.0.34.2")])
    c = check("baseline", cids())

    # 1. new XRd cabled to a running XRd: only the new device boots
    node("R3", XRD, 100)
    link("R3", "GigabitEthernet0/0/0/0", "R1", "GigabitEthernet0/0/0/1")
    wait_synced(); c = check("add R3 + cable R3-R1", c)
    configure("R1", "interface GigabitEthernet0/0/0/1\n ipv4 address 10.0.13.1 255.255.255.0\n no shutdown\n")
    configure("R3", "interface GigabitEthernet0/0/0/0\n ipv4 address 10.0.13.3 255.255.255.0\n no shutdown\n")
    EXPECT["R1"].append("10.0.13.1"); EXPECT["R3"] = ["10.0.13.3"]; PINGS.append(("R3", "10.0.13.1"))
    c = check("R3-R1 configured", c)

    # 2. new FRR cabled to a running FRR
    node("F3", FRR, 500)
    l_f3 = link("F3", "eth1", "F1", "eth2")
    wait_synced(); c = check("add F3 + cable F3-F1", c)
    configure("F1", "interface eth2\n ip address 10.0.35.1/24\n")
    configure("F3", "interface eth1\n ip address 10.0.35.3/24\n")
    EXPECT["F1"].append("10.0.35.1"); EXPECT["F3"] = ["10.0.35.3"]; PINGS.append(("F3", "10.0.35.1"))
    c = check("F3-F1 configured", c)

    # 3. cable between two running devices of different kinds (XRd <-> FRR)
    link("R2", "GigabitEthernet0/0/0/1", "F2", "eth2")
    wait_synced()
    configure("R2", "interface GigabitEthernet0/0/0/1\n ipv4 address 10.0.26.2 255.255.255.0\n no shutdown\n")
    configure("F2", "interface eth2\n ip address 10.0.26.6/24\n")
    EXPECT["R2"].append("10.0.26.2"); PINGS.append(("R2", "10.0.26.6"))
    c = check("hot cable R2-F2", c)

    # 4. unplug, then replug a cable
    call("DELETE", f"/topologies/{tid}/links/{l_f3}")
    PINGS.remove(("F3", "10.0.35.1")); NO_PINGS.append(("F3", "10.0.35.1"))
    wait_synced(); c = check("unplug F3-F1", c)
    l_f3 = link("F3", "eth1", "F1", "eth2")
    NO_PINGS.remove(("F3", "10.0.35.1")); PINGS.append(("F3", "10.0.35.1"))
    wait_synced(); c = check("replug F3-F1", c)

    # 5. remove a device
    call("DELETE", f"/topologies/{tid}/nodes/{N['R3']}")
    del N["R3"]; EXPECT.pop("R3"); PINGS.remove(("R3", "10.0.13.1"))
    wait_synced(); c = check("remove node R3", c)

    # 6. a Lab Switch (internal L2 segment) between an XRd and an FRR
    sw = call("POST", f"/topologies/{tid}/nodes/", json={"template_id": TPL["Lab Switch"], "x": 500, "y": 300})
    N["SW"] = sw["id"]
    link("R2", "GigabitEthernet0/0/0/3", "SW", None); link("F2", "eth4", "SW", None)
    wait_synced()
    configure("R2", "interface GigabitEthernet0/0/0/3\n ipv4 address 10.0.40.2 255.255.255.0\n no shutdown\n")
    configure("F2", "interface eth4\n ip address 10.0.40.6/24\n")
    PINGS.append(("R2", "10.0.40.6"))
    c = check("Lab Switch R2-SW-F2", c)

    if WITH_VM:
        # 7. a VM (Catalyst 8000v) cabled to a running XRd
        node("C1", C8K, 300)
        l_c1 = link("C1", "GigabitEthernet2", "R2", "GigabitEthernet0/0/0/2")
        wait_synced(); c = check("add C1 (c8000v) + cable C1-R2", c, allow_recreate=("C1",))
        configure("C1", "interface GigabitEthernet2\n ip address 10.0.25.5 255.255.255.0\n no shutdown\n"
                        "interface Loopback0\n ip address 5.5.5.5 255.255.255.255\n")
        configure("R2", "interface GigabitEthernet0/0/0/2\n ipv4 address 10.0.25.2 255.255.255.0\n no shutdown\n")
        EXPECT["C1"] = ["10.0.25.5", "5.5.5.5"]; EXPECT["R2"].append("10.0.25.2"); PINGS.append(("C1", "10.0.25.2"))
        c = check("C1 configured live", c)

        # 8. new cable on the running VM: no restart at all now
        link("C1", "GigabitEthernet3", "F2", "eth3")
        wait_synced()
        configure("C1", "interface GigabitEthernet3\n ip address 10.0.27.5 255.255.255.0\n no shutdown\n")
        configure("F2", "interface eth3\n ip address 10.0.27.6/24\n")
        PINGS.append(("C1", "10.0.27.6"))
        c = check("hot cable C1-F2 (VM)", c)

        # 9. unplug / replug the VM's cable
        call("DELETE", f"/topologies/{tid}/links/{l_c1}")
        PINGS.remove(("C1", "10.0.25.2")); NO_PINGS.append(("C1", "10.0.25.2"))
        wait_synced(); c = check("unplug C1-R2", c)
        link("C1", "GigabitEthernet2", "R2", "GigabitEthernet0/0/0/2")
        NO_PINGS.remove(("C1", "10.0.25.2")); PINGS.append(("C1", "10.0.25.2"))
        wait_synced(); c = check("replug C1-R2", c)
finally:
    print("\n==== SUMMARY")
    for step, rec, add, fails in RESULTS:
        print(f"{'PASS' if not fails else 'FAIL'}  {step:32} recreated={rec} added={add} {fails or ''}")
    if "--keep" not in sys.argv:
        try:
            call("POST", f"/topologies/{tid}/lab/destroy", timeout=600)
        except Exception as e:
            print("destroy:", e)
        call("DELETE", f"/topologies/{tid}")
