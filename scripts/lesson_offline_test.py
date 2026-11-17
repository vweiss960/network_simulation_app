#!/usr/bin/env python3
"""Verify a lesson pack on real devices without the app: build its topology
as a throwaway containerlab lab, apply the starting configs, then for each
task apply the reference solution and poll that task's checks (the app's own
evaluator) until they pass or time out.

Run inside the backend container (it has containerlab, asyncssh and the app):

  docker exec plabs-backend-1 sh -c 'cd /home/vincent/gitRepos/network_simulation_app/backend && \
      PYTHONPATH=. python ../scripts/lesson_offline_test.py ../curriculum/lessons/<slug> [--keep] [--from TASK]'

The lab directory must be on a path that exists on the host too (bind mounts):
it goes under $LAB_STATE_DIR/offline-<slug>. `--keep` leaves the lab running
(re-run with `--reuse` to skip the deploy); otherwise it's destroyed at the end.
Exit status: 0 when every task's checks pass.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import yaml

from app.services import device_exec as dx
from app.services import lesson_checks as lc

MGMT = "172.31.252"
CREDS = {"cisco_xrd": ("clab", "clab@123")}
FAMILY = {"cisco_xrd": "iosxr", "linux": "linux"}


def kernel(kind: str, ifname: str) -> str:
    if kind == "cisco_xrd":                          # GigabitEthernet0/0/0/1 → Gi0-0-0-1
        return "Gi" + ifname.split("GigabitEthernet", 1)[1].replace("/", "-")
    if kind.startswith("cisco_") and ifname.startswith("GigabitEthernet"):   # vrnetlab: Gi2 → eth1
        return f"eth{int(ifname[len('GigabitEthernet'):]) - 1}"
    return ifname


def target(lab: str, n: dict, ip: str) -> dx.Target:
    kind = n["kind"]
    user, pw = CREDS.get(kind, ("admin", "admin"))
    fam = FAMILY.get(kind, "iosxe")
    if kind == "linux":
        fam = "linux"
    return dx.Target(node_id=0, name=n["name"], kind=kind, family=fam, host=ip, username=user, password=pw,
                     container=f"clab-{lab}-{n['name']}", image=n.get("image", ""))


def sh(cmd: str, check=True) -> str:
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if check and r.returncode:
        raise SystemExit(f"$ {cmd}\n{r.stdout}\n{r.stderr}")
    return r.stdout


def wait_ssh(t: dx.Target, timeout=1200) -> None:
    end = time.time() + timeout
    while time.time() < end:
        try:
            asyncio.run(dx.run_cli(t, ["show version | include uptime" if t.family == "iosxe" else "show version"], timeout=40))
            return
        except Exception:                            # noqa: BLE001 — still booting
            time.sleep(10)
    raise SystemExit(f"{t.name}: no SSH after {timeout}s")


def configure(t: dx.Target, cfg: str) -> None:
    for attempt in range(1, 6):
        try:
            r = asyncio.run(dx.configure(t, cfg, timeout=180))
        except dx.ExecError as e:
            if attempt < 5 and re.search(r"reset|refused|closed|timed out|booting", str(e), re.I):
                time.sleep(10)
                continue
            raise
        if not r["ok"]:
            print(f"    ! {t.name}: config errors: {'; '.join(r['errors'])[:600]}", flush=True)
        return


def run_checks(targets: dict, checks: list[dict]) -> list[dict]:
    by_dev: dict[str, list] = {}
    for c in checks:
        d = lc.check_devices(c)[0]
        by_dev.setdefault(d, []).append(c)

    async def one(dev, cs):
        t = targets[dev]
        cmds = list(dict.fromkeys(lc._command(c, t) for c in cs))
        try:
            out = await lc._run_device(t, cmds)
        except Exception as e:                        # noqa: BLE001
            return {id(c): {"ok": False, "describe": lc.describe(c), "detail": str(e)[:300], "output": ""} for c in cs}
        return {id(c): lc._evaluate(c, t, out.get(lc._command(c, t), "")) for c in cs}

    async def all_():
        rs = await asyncio.gather(*(one(d, cs) for d, cs in by_dev.items()))
        m = {}
        for r in rs:
            m.update(r)
        return [m[id(c)] for c in checks]
    return asyncio.run(all_())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pack")
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--reuse", action="store_true", help="the lab is already up and configured up to --from")
    ap.add_argument("--from", dest="start", default="")
    ap.add_argument("--timeout", type=int, default=240, help="seconds to wait for a task's checks")
    a = ap.parse_args()
    pack = Path(a.pack)
    topo = yaml.safe_load((pack / "topology.yaml").read_text())
    tasks = yaml.safe_load((pack / "tasks.yaml").read_text())
    slug = pack.name
    lab = f"off-{slug}"[:40]
    labdir = Path(os.environ.get("LAB_STATE_DIR", "/tmp")) / f"offline-{slug}"
    labdir.mkdir(parents=True, exist_ok=True)
    nodes = {n["name"]: n for n in topo["nodes"]}
    ips = {name: f"{MGMT}.{10 + i}" for i, name in enumerate(nodes)}
    targets = {name: target(lab, n, ips[name]) for name, n in nodes.items()}

    if not a.reuse:
        clab = {"name": lab, "mgmt": {"network": f"{lab}-mgmt", "ipv4-subnet": f"{MGMT}.0/24"},
                "topology": {"nodes": {}, "links": []}}
        for name, n in nodes.items():
            clab["topology"]["nodes"][name] = {"kind": n["kind"], "image": n["image"], "mgmt-ipv4": ips[name]}
        for l in topo.get("links") or []:
            a1, i1, b1, j1 = l[:4]
            clab["topology"]["links"].append({"endpoints": [f"{a1}:{kernel(nodes[a1]['kind'], i1)}",
                                                            f"{b1}:{kernel(nodes[b1]['kind'], j1)}"]})
        (labdir / "lab.clab.yml").write_text(yaml.safe_dump(clab, sort_keys=False))
        print(f"== deploying {lab} ({len(nodes)} nodes)", flush=True)
        sh(f"cd {labdir} && containerlab deploy -t lab.clab.yml --reconfigure")
        t0 = time.time()
        for name, n in nodes.items():
            t = targets[name]
            if n["kind"] == "linux":
                for line in (n.get("startup_config") or "").splitlines():
                    if line.strip() and not line.strip().startswith("#"):
                        sh(f"docker exec {t.container} sh -c {lc_quote(line.strip())}", check=False)
        for name, n in nodes.items():
            t = targets[name]
            if n["kind"] == "linux" or not (n.get("startup_config") or "").strip():
                continue
            wait_ssh(t)
            print(f"   {name} up after {time.time() - t0:.0f}s — starting config", flush=True)
            configure(t, n["startup_config"])

    failed = []
    started = not a.start
    for task in tasks:
        if not started:
            started = task["id"] == a.start
            if not started:
                continue
        print(f"\n== task {task['id']}: {task['title']}", flush=True)
        for dev, cfg in (task.get("solution") or {}).items():
            print(f"   applying solution on {dev}", flush=True)
            configure(targets[dev], cfg)
        checks = task.get("checks") or []
        if not checks:
            continue
        t1 = time.time()
        while True:
            res = run_checks(targets, checks)
            bad = [r for r in res if not r["ok"]]
            if not bad or time.time() - t1 > a.timeout:
                break
            time.sleep(15)
        for r in res:
            print(f"   {'✓' if r['ok'] else '✗'} {r['describe']}" + ("" if r["ok"] else f"  — {r['detail']}"), flush=True)
            if not r["ok"] and r.get("output"):
                print("      " + r["output"][:1200].replace("\n", "\n      "), flush=True)
        print(f"   ({time.time() - t1:.0f}s)", flush=True)
        if bad:
            failed.append(task["id"])

    print("\n== result: " + ("ALL TASKS PASS" if not failed else f"FAILED: {', '.join(failed)}"), flush=True)
    if not a.keep:
        sh(f"cd {labdir} && containerlab destroy -t lab.clab.yml --cleanup", check=False)
        sh(f"rm -rf {labdir}", check=False)
    return 0 if not failed else 1


def lc_quote(s: str) -> str:
    return "'" + s.replace("'", "'\"'\"'") + "'"


if __name__ == "__main__":
    sys.exit(main())
