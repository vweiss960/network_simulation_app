#!/usr/bin/env python3
"""Push interface config to Phanero via the Service module API.

Renders + deploys the "Interface Provisioning" service to one or more devices.
Zero dependencies (stdlib only).

Usage:
    python3 phanero_push_interface.py --dry-run     # preview rendered config, no push
    python3 phanero_push_interface.py               # render + push over SSH

Override defaults with env vars or the flags below.
"""
import argparse
import json
import os
import urllib.request
import urllib.error

# ---- Config (override via env or flags) ----
BASE_URL = os.environ.get("PHANERO_URL", "http://localhost:8000")
USERNAME = os.environ.get("PHANERO_USER", "admin")
PASSWORD = os.environ.get("PHANERO_PASS", "admin")
SERVICE_NAME = os.environ.get("PHANERO_SERVICE", "Interface Provisioning")
TOPOLOGY_ID = int(os.environ.get("PHANERO_TOPOLOGY_ID", "6"))

# ---- The config to push (one entry per device) ----
DEPLOYMENTS = [
    {
        "device": "S-RR-1",
        "variables": {
            "name": "GigabitEthernet0/0/0/3",
            "ipv4": "10.1.1.1",
            "netmask": "255.255.255.252",
        },
    }
]


def _api(path, token=None, method="GET", body=None):
    url = f"{BASE_URL}/api{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raise SystemExit(f"API {method} {path} failed: {e.code} {e.read().decode()[:300]}")
    except urllib.error.URLError as e:
        raise SystemExit(f"Cannot reach Phanero at {BASE_URL}: {e}")


def login():
    return _api("/auth/login", method="POST",
                body={"username": USERNAME, "password": PASSWORD})["access_token"]


def find_service_id(token):
    for s in _api("/services", token=token):
        if s["name"] == SERVICE_NAME:
            return s["id"]
    raise SystemExit(f"Service '{SERVICE_NAME}' not found. Existing: "
                     f"{[s['name'] for s in _api('/services', token=token)]}")


def main():
    ap = argparse.ArgumentParser(description="Push interface config to Phanero.")
    ap.add_argument("--dry-run", action="store_true", help="Render only; do not push to devices.")
    ap.add_argument("--target-mode", default=None, choices=["lab", "mgmt_ip"],
                    help="Override the service's default target (lab node vs mgmt IP).")
    ap.add_argument("--username", help="SSH username (mgmt_ip mode).")
    ap.add_argument("--password", help="SSH password (mgmt_ip mode).")
    args = ap.parse_args()

    token = login()
    sid = find_service_id(token)

    payload = {"topology_id": TOPOLOGY_ID, "deployments": DEPLOYMENTS}
    if args.target_mode:
        payload["target_mode"] = args.target_mode
    if args.username:
        payload["credentials"] = {"username": args.username, "password": args.password or ""}

    if args.dry_run:
        print(f"Preview '{SERVICE_NAME}' (service {sid}, topology {TOPOLOGY_ID}) — no push:\n")
        res = _api(f"/services/{sid}/render", token=token, method="POST", body=payload)
    else:
        print(f"Deploying '{SERVICE_NAME}' (service {sid}, topology {TOPOLOGY_ID}):\n")
        payload["dry_run"] = False
        res = _api(f"/services/{sid}/deploy", token=token, method="POST", body=payload)

    ok_all = True
    for r in res["results"]:
        status = "OK" if r.get("ok") else "FAILED"
        ok_all = ok_all and r.get("ok", False)
        print(f"=== {r['device']}: {status} ===")
        if r.get("rendered"):
            print(r["rendered"])
        if r.get("error"):
            print(f"  error: {r['error']}")
        if r.get("output"):  # device transcript (deploy only)
            print("  --- device transcript ---")
            print("  " + r["output"].replace("\n", "\n  ").rstrip())
        print()

    raise SystemExit(0 if ok_all else 1)


if __name__ == "__main__":
    main()
