#!/usr/bin/env python3
"""Export / import a Phanero service — for moving a service between instances.

Wraps the existing GET /api/services/{id}/export and POST /api/services/import
endpoints. Zero dependencies (stdlib only).

  # Export a service (by name or id) from THIS instance to a JSON file:
  python3 phanero_service_io.py export "Interface Provisioning" -o svc.json

  # Import that JSON into ANOTHER instance (point PHANERO_URL at it):
  PHANERO_URL=https://other-host python3 phanero_service_io.py import svc.json

Auth + target host come from env: PHANERO_URL / PHANERO_USER / PHANERO_PASS.
On import, a name clash is auto-suffixed ("… (2)") rather than overwriting.
"""
import argparse
import json
import os
import urllib.request
import urllib.error

BASE_URL = os.environ.get("PHANERO_URL", "http://localhost:8000")
USERNAME = os.environ.get("PHANERO_USER", "admin")
PASSWORD = os.environ.get("PHANERO_PASS", "admin")


def _api(path, token=None, method="GET", body=None):
    url = f"{BASE_URL}/api{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raise SystemExit(f"API {method} {path} failed: {e.code} {e.read().decode()[:300]}")
    except urllib.error.URLError as e:
        raise SystemExit(f"Cannot reach Phanero at {BASE_URL}: {e}")


def login():
    return _api("/auth/login", method="POST",
                body={"username": USERNAME, "password": PASSWORD})["access_token"]


def cmd_export(args):
    token = login()
    services = _api("/services", token=token)
    match = next((s for s in services if s["name"] == args.service or str(s["id"]) == args.service), None)
    if not match:
        raise SystemExit(f"Service '{args.service}' not found. Existing: {[s['name'] for s in services]}")
    data = _api(f"/services/{match['id']}/export", token=token)
    out = args.output or f"{match['name']}.service.json"
    with open(out, "w") as f:
        json.dump(data, f, indent=2)
    print(f"Exported '{match['name']}' from {BASE_URL} -> {out} "
          f"({len(data.get('templates', []))} template(s), {len(data.get('variables', []))} variable(s))")


def cmd_import(args):
    token = login()
    with open(args.file) as f:
        payload = json.load(f)
    s = _api("/services/import", token=token, method="POST", body=payload)
    print(f"Imported as '{s['name']}' (id {s['id']}) on {BASE_URL}")


def main():
    ap = argparse.ArgumentParser(description="Export/import a Phanero service between instances.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    pe = sub.add_parser("export", help="Export a service to a JSON file.")
    pe.add_argument("service", help="Service name or id.")
    pe.add_argument("-o", "--output", help="Output file (default: '<name>.service.json').")
    pe.set_defaults(func=cmd_export)

    pi = sub.add_parser("import", help="Import a service JSON file into the target instance.")
    pi.add_argument("file", help="Path to a service JSON file (from export).")
    pi.set_defaults(func=cmd_import)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
