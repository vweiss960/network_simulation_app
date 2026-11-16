#!/usr/bin/env python3
"""Prove a troubleshooting scenario's fault catalog on real devices.

    python3 scripts/chaos_catalog_test.py <jwt> <scenario-slug> [--only ID,…] [--skip ID,…] [--keep]

--only / --skip take template ids (isis-auth) or candidate ids
(isis-auth@R1:GigabitEthernet0/0/0/0), comma-separated.

Uses the scenario's admin test lab: deploys the healthy baseline, checks that
every health check passes, then for each candidate fault (template × target):
  1. inject it — the configs must apply without errors;
  2. at least one of its symptom checks must start failing (≤ 150 s);
  3. revert it — every health check must pass again (≤ 5 min).
The lab is wiped and deleted at the end unless --keep.
"""
import json
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8100/api"


def api(tok, method, path, body=None, timeout=900):
    req = urllib.request.Request(BASE + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        raise SystemExit(f"{method} {path}: HTTP {e.code} {e.read()[:400]!r}")


def wait(fn, timeout, every=10):
    deadline = time.time() + timeout
    while True:
        ok, val = fn()
        if ok or time.time() > deadline:
            return ok, val
        time.sleep(every)


def main():
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    tok, slug = sys.argv[1], sys.argv[2]
    keep = "--keep" in sys.argv
    def ids(flag):
        return set(sys.argv[sys.argv.index(flag) + 1].split(",")) if flag in sys.argv else set()
    only, skip = ids("--only"), ids("--skip")
    sc = next((s for s in api(tok, "GET", "/troubleshooting") if s["slug"] == slug), None)
    if not sc:
        raise SystemExit(f"No scenario {slug!r}")
    sid = sc["id"]
    full = api(tok, "GET", f"/troubleshooting/{sid}?full=true")
    tid = api(tok, "POST", f"/troubleshooting/{sid}/test-lab")["topology_id"]
    print(f"Scenario {full['title']!r}: test lab {tid}")
    t0 = time.time()
    results = []

    def health():
        res = api(tok, "POST", f"/troubleshooting/{sid}/test-lab/checks", {"health": True})["results"]
        bad = [r for r in res if not r["ok"]]
        return not bad, bad

    try:
        print("Deploying…")
        api(tok, "POST", f"/topologies/{tid}/lab/deploy?all=true", timeout=1800)
        nodes = {n["id"]: n["name"] for n in api(tok, "GET", f"/topologies/{tid}/nodes/")}

        def up():
            st = api(tok, "GET", f"/topologies/{tid}/lab/status")
            ready = {nodes.get(int(k)) for k, v in st["nodes"].items() if v.get("ready_state") == "up"}
            return set(nodes.values()) <= ready, None
        if not wait(up, 900)[0]:
            raise SystemExit("Devices not up after 15 min")
        print(f"All devices up ({time.time() - t0:.0f}s)")
        ok, bad = wait(health, 480, 20)
        if not ok:
            for b in bad:
                print(f"  ✗ {b['describe']} — {b['detail']}")
            raise SystemExit("The baseline isn't healthy")
        print(f"Baseline healthy ({time.time() - t0:.0f}s)\n")

        cands = [c for c in full["candidates"]
                 if (not only or c["id"] in only or c["template"] in only)
                 and c["id"] not in skip and c["template"] not in skip]
        print(f"{len(cands)} candidate fault(s)\n")
        for n, c in enumerate(cands, 1):
            key = c["id"]
            t1 = time.time()
            print(f"[{n}/{len(cands)}] {key} — {c['protocol']} · {c['category']} · {c['family']}", flush=True)
            r = api(tok, "POST", f"/troubleshooting/{sid}/test-lab/fault", {"fault": key, "action": "inject"})
            names, symptoms = r["names"], r["symptoms"]
            status = "ok"
            if r["errors"]:
                print(f"  ✗ inject errors: {r['errors']}")
                status = "inject-error"

            def symptom():
                res = api(tok, "POST", f"/troubleshooting/{sid}/test-lab/checks", {"checks": symptoms})["results"]
                return any(not x["ok"] for x in res), res
            showed, res = wait(symptom, 150)
            if showed:
                print(f"  ✓ symptom showed ({time.time() - t1:.0f}s): "
                      + "; ".join(x["describe"] for x in res if not x["ok"]))
            else:
                print("  ✗ no symptom showed within 150 s")
                status = "silent" if status == "ok" else status
            r = api(tok, "POST", f"/troubleshooting/{sid}/test-lab/fault",
                    {"fault": key, "action": "revert", "names": names})
            if r["errors"]:
                print(f"  ✗ revert errors: {r['errors']}")
                status = "revert-error"
            ok, bad = wait(health, 300, 15)
            if ok:
                print(f"  ✓ healthy again ({time.time() - t1:.0f}s)", flush=True)
            else:
                print("  ✗ not healthy after revert: " + "; ".join(b["describe"] for b in bad), flush=True)
                status = "not-healthy" if status == "ok" else status
                ok, bad = wait(health, 300, 20)            # give it one more chance before the next fault
            results.append((key, status))
    finally:
        print("\nSummary:")
        for key, status in results:
            print(f"  {'PASS' if status == 'ok' else 'FAIL'} {key}{'' if status == 'ok' else ' — ' + status}")
        if not keep:
            print("Wiping the test lab…")
            api(tok, "POST", f"/topologies/{tid}/lab/destroy", timeout=900)
            api(tok, "DELETE", f"/topologies/{tid}")
    bad = [k for k, s in results if s != "ok"]
    print(f"\n{'ALL FAULTS VERIFIED' if not bad else f'{len(bad)} FAULT(S) FAILED'} in {time.time() - t0:.0f}s")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
