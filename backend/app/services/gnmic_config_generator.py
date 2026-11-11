"""Generate a gnmic collector config for a deployed lab.

The collector runs as a sidecar on the containerlab `clab` bridge, dials each
lab node's gNMI endpoint (plaintext / no-TLS on :9339 for XRd), and exposes a
Prometheus output that Prometheus scrapes. This mirrors how
prometheus_config_generator.py builds scrape config from node inventory — but
for gNMI streaming telemetry instead of SNMP.

Lab-telemetry add-on only (gated by PHANERO_LAB_TELEMETRY_ENABLED).
"""
from __future__ import annotations

import re

import yaml

# gNMI plaintext port configured on XRd nodes by clab_yaml._xrd_clab_defaults.
DEFAULT_GNMI_PORT = 9339
# Default lab creds (XRd clab defaults). Configurable via env in lab_addons.
DEFAULT_USER = "clab"
DEFAULT_PASS = "clab@123"
# Where the collector's Prometheus output listens (inside the sidecar).
PROM_LISTEN = "0.0.0.0:9273"

# A small, broadly-supported default subscription: OpenConfig interface state
# counters (in/out octets, packets, errors) sampled periodically. Kept minimal
# so it works across kinds; extend per-kind later if needed.
DEFAULT_SUBSCRIPTION_PATHS = [
    "openconfig-interfaces:/interfaces/interface/state/counters",
]


def _gnmic_sub_key(name: str) -> str:
    """Sanitize a user subscription name into a safe gnmic subscription key.

    gnmic normalizes each target's subscription reference (splitting on
    whitespace and lowercasing) but matches it against the case-sensitive
    subscription-map key, so any name with a space or an uppercase letter fails
    to match its definition and the subscription is silently dropped (it only
    appears to work in isolation because gnmic falls back to subscribing to all
    subscriptions when a target matches none). Lowercase and collapse anything
    outside [a-z0-9_.-] to '_' so the reference and the key always agree. The
    user's display name is unaffected (it lives in the DB); this only touches
    the generated gnmic config key.
    """
    key = re.sub(r"[^0-9a-z_.-]+", "_", (name or "").strip().lower()).strip("_")
    return key or "sub"


def _subscription_entry(path: str, mode: str, sample_interval: str) -> dict:
    """One gnmic subscription definition. mode "on-change" streams on change;
    anything else streams sampled at sample_interval."""
    entry = {"paths": [path], "mode": "stream", "encoding": "json_ietf"}
    if mode == "on-change":
        entry["stream-mode"] = "on-change"
    else:
        entry["stream-mode"] = "sample"
        entry["sample-interval"] = sample_interval or "30s"
    return entry


def build_gnmic_config(
    nodes: list[dict],
    username: str = DEFAULT_USER,
    password: str = DEFAULT_PASS,
    port: int = DEFAULT_GNMI_PORT,
    sample_interval: str = "30s",
    extra_subscriptions: list[dict] | None = None,
) -> str:
    """Return a gnmic config YAML string.

    nodes: [{"name": <clab short/router name>, "mgmt_ip": <ip>}], only entries
    with a mgmt_ip are included. Each becomes a gnmic target at mgmt_ip:port with
    plaintext gRPC (insecure); the Prometheus output exposes all series at
    :9273/metrics.

    extra_subscriptions: user-defined subs, each {name, path, mode, sample_interval},
    injected alongside the built-in if-counters subscription.
    """
    extra = [s for s in (extra_subscriptions or []) if s.get("name") and s.get("path")]
    # Assign each extra sub a sanitized, unique gnmic key (see _gnmic_sub_key).
    used_keys = {"if-counters"}
    keyed_extra: list[tuple[str, dict]] = []
    for s in extra:
        base = _gnmic_sub_key(s["name"])
        key, n = base, 2
        while key in used_keys:
            key, n = f"{base}_{n}", n + 1
        used_keys.add(key)
        keyed_extra.append((key, s))
    sub_names = ["if-counters"] + [k for k, _ in keyed_extra]

    targets: dict[str, dict] = {}
    for n in nodes:
        ip = (n.get("mgmt_ip") or "").strip()
        if not ip:
            continue
        targets[f"{ip}:{port}"] = {
            "username": username,
            "password": password,
            "insecure": True,           # plaintext gRPC — XRd runs gNMI no-tls
            "subscriptions": list(sub_names),
            # Label series with the node name so PromQL can join to the topology.
            "event-tags": {"source_name": n.get("name", ip)},
        }

    subscriptions = {
        "if-counters": {
            "paths": DEFAULT_SUBSCRIPTION_PATHS,
            "mode": "stream",
            "stream-mode": "sample",
            "sample-interval": sample_interval,
            "encoding": "json_ietf",
        },
    }
    for key, s in keyed_extra:
        subscriptions[key] = _subscription_entry(
            s["path"], s.get("mode", "sample"), s.get("sample_interval", "30s"))

    cfg = {
        "targets": targets,
        "subscriptions": subscriptions,
        "outputs": {
            "prom": {
                "type": "prometheus",
                "listen": PROM_LISTEN,
                "path": "/metrics",
                # Strip the gNMI path prefix into metric names + keep tags as labels.
                "strings-as-labels": True,
            },
        },
    }
    return yaml.safe_dump(cfg, sort_keys=False, default_flow_style=False)
