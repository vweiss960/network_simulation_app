"""Recommended resource defaults per containerlab kind.

Used by the frontend to auto-populate template fields when the user picks a
kind, and by the resource estimate as a fallback when a template hasn't set
min_memory_mb / min_cpu_cores explicitly.

Values are conservative steady-state minimums based on each platform's
publicly-documented lab requirements. They are not guarantees — heavy
data-plane workloads will push higher and the operator can always override
on the template.
"""

# Each entry: clab kind → recommended defaults.
CLAB_KIND_DEFAULTS: dict[str, dict] = {
    "linux": {
        "min_memory_mb": 512,
        "min_cpu_cores": 1,
        "requires_hugepages": False,
        "description": "Generic Linux container — FRR, Quagga, custom images",
    },
    "cisco_xrd": {
        "min_memory_mb": 2048,
        "min_cpu_cores": 2,
        "requires_hugepages": False,
        "description": "Cisco XRd Control Plane — clab's cisco_xrd kind (vRouter needs SR-IOV and isn't supported)",
    },
    "cisco_xrv9k": {
        "min_memory_mb": 16384,
        "min_cpu_cores": 4,
        "requires_hugepages": False,
        "description": "Cisco XRv9000 — heavyweight KVM-based vRouter",
    },
    "arista_ceos": {
        "min_memory_mb": 2048,
        "min_cpu_cores": 1,
        "requires_hugepages": False,
        "description": "Arista cEOS-lab — free with Arista account",
    },
    "nokia_srlinux": {
        "min_memory_mb": 2048,
        "min_cpu_cores": 1,
        "requires_hugepages": False,
        "description": "Nokia SR Linux — free, well-suited for IP fabric labs",
    },
    "juniper_crpd": {
        "min_memory_mb": 1024,
        "min_cpu_cores": 1,
        "requires_hugepages": False,
        "description": "Juniper cRPD — control-plane only, requires license",
    },
}


def get_defaults_for_kind(kind: str) -> dict:
    """Return the defaults dict for a kind, or empty dict if unknown."""
    return CLAB_KIND_DEFAULTS.get(kind, {})


def apply_defaults_to_template_fields(kind: str, current: dict) -> dict:
    """Fill in min_memory_mb / min_cpu_cores / requires_hugepages from defaults
    only when the current value is unset (0 or False). Returns a new dict
    suitable for merging back into a template's lab config.

    Used by the estimate path so a template with kind set but no resource
    fields still gets a reasonable estimate without forcing the user to fill
    in every value.
    """
    d = get_defaults_for_kind(kind)
    if not d:
        return dict(current)
    out = dict(current)
    if not out.get("min_memory_mb"):
        out["min_memory_mb"] = d["min_memory_mb"]
    if not out.get("min_cpu_cores"):
        out["min_cpu_cores"] = d["min_cpu_cores"]
    if not out.get("requires_hugepages"):
        out["requires_hugepages"] = d["requires_hugepages"]
    return out
