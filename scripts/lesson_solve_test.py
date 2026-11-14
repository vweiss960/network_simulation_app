#!/usr/bin/env python3
"""Solve a lesson end to end on real devices, to prove its checks are right.

    python3 scripts/lesson_solve_test.py <jwt> <lesson-slug> [--keep]

Starts the lesson (a fresh attempt lab for the token's user), deploys it,
waits for every device, then for each task in order:
  - runs the task's checks before applying anything (reported, not asserted
    except for the first task, which must fail on the starting state);
  - applies the task's reference solution to each device;
  - re-runs the checks until they pass (OSPF needs a moment to converge).
Finally every task is checked again. The lab is wiped and its topology
deleted unless --keep.
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


def show(task_id, ran):
    for c in ran["checks"]:
        print(f"    {'✓' if c['ok'] else '✗'} {c['describe']}" + ("" if c["ok"] else f" — {c['detail']}"))


def main():
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    tok, slug, keep = sys.argv[1], sys.argv[2], "--keep" in sys.argv
    lesson = next((l for l in api(tok, "GET", "/lessons") if l["slug"] == slug), None)
    if not lesson:
        raise SystemExit(f"No lesson {slug!r}")
    full = api(tok, "GET", f"/lessons/{lesson['id']}?full=true")
    start = api(tok, "POST", f"/lessons/{lesson['id']}/start")
    att = start["attempt"]
    tid = att["topology_id"]
    if not start["created"]:
        print("Resetting the existing attempt…")
        att = api(tok, "POST", f"/lesson-attempts/{att['id']}/reset")
    print(f"Lesson {full['title']!r}: attempt {att['id']}, lab {tid}")
    ok_all = True
    t0 = time.time()
    try:
        print("Deploying…")
        api(tok, "POST", f"/topologies/{tid}/lab/deploy?all=true", timeout=1800)
        nodes = {n["id"]: n["name"] for n in api(tok, "GET", f"/topologies/{tid}/nodes/")}
        for _ in range(120):
            st = api(tok, "GET", f"/topologies/{tid}/lab/status")
            up = {nodes.get(int(k)) for k, v in st["nodes"].items() if v.get("ready_state") == "up"}
            if set(nodes.values()) <= up:
                break
            time.sleep(5)
        else:
            raise SystemExit(f"Devices not up after 10 min: {set(nodes.values()) - up}")
        print(f"All devices up ({time.time() - t0:.0f}s)")
        by_name = {v: k for k, v in nodes.items()}

        for i, task in enumerate(full["tasks"]):
            print(f"\n[{i + 1}] {task['title']}")
            before = api(tok, "POST", f"/lesson-attempts/{att['id']}/check?task={task['id']}")["ran"][task["id"]]
            print(f"  before: {'PASS' if before['passed'] else 'fail'}")
            if i == 0 and before["passed"]:
                print("  ✗ the first task passes on the starting state — its checks don't test anything")
                ok_all = False
            for dev, cfg in task["solution"].items():
                r = api(tok, "POST", f"/topologies/{tid}/nodes/{by_name[dev]}/configure", {"config": cfg})
                print(f"  applied on {dev}: {'ok' if r.get('ok') else 'ERRORS ' + str(r.get('errors'))}")
            deadline, res = time.time() + 120, None
            while time.time() < deadline:
                res = api(tok, "POST", f"/lesson-attempts/{att['id']}/check?task={task['id']}")["ran"][task["id"]]
                if res["passed"]:
                    break
                time.sleep(8)
            print(f"  after:  {'PASS' if res['passed'] else 'FAIL'}")
            show(task["id"], res)
            if not res["passed"]:
                ok_all = False
                for c in res["checks"]:
                    if not c["ok"] and c.get("output"):
                        print("    output:\n      " + c["output"][:800].replace("\n", "\n      "))

        print("\nFinal state (passes are kept; Check all re-runs only unpassed tasks):")
        final = api(tok, "POST", f"/lesson-attempts/{att['id']}/check")
        for t in full["tasks"]:
            r = final["attempt"]["results"].get(t["id"], {})
            print(f"  {'PASS' if r.get('passed') else 'FAIL'} {t['id']}{' (changed by later tasks)' if r.get('changed_since') else ''}")
            ok_all &= bool(r.get("passed"))
        print(f"Attempt complete: {bool(final['attempt']['completed_at'])}")
    finally:
        if not keep:
            print("Wiping the lab…")
            api(tok, "POST", f"/topologies/{tid}/lab/destroy", timeout=900)
            api(tok, "DELETE", f"/topologies/{tid}")
    print(f"\n{'ALL TASKS SOLVED' if ok_all else 'SOME TASKS FAILED'} in {time.time() - t0:.0f}s")
    sys.exit(0 if ok_all else 1)


if __name__ == "__main__":
    main()
