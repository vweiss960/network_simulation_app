"""Device kind registry — the single source of truth for every containerlab
kind Phanero Labs can deploy.

Each node on the canvas *is* a deployable device: it carries a containerlab
`kind` and an image. Everything kind-specific (how data interfaces are named
in the clab YAML vs. inside the device's own config, default credentials,
how to open a CLI, how long it takes to boot, how much RAM/CPU it wants)
lives here instead of being scattered across the lab modules.

Interface naming
----------------
Every data-plane port has two names:
  * ``kernel`` — the name containerlab uses in ``links: endpoints:`` (what the
    veth inside the container is called, e.g. ``Gi0-0-0-0`` or ``eth1``);
  * ``name``   — the name the device's own CLI/config uses
    (``GigabitEthernet0/0/0/0``, ``GigabitEthernet2`` …).
Nodes store both (``NodeInstance.interfaces``) so links, configs and the UI
all speak the device's native names while the YAML generator uses kernel names.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


def _pattern(fmt: str) -> Callable[[int], str]:
    """Interface formatter from a pattern. {n0}=0-based, {n1}=1-based,
    {n2}=index+2 (IOS-XE VMs whose Gi1 is the management port)."""
    return lambda i: fmt.format(n0=i, n1=i + 1, n2=i + 2)


def _vios_name(i: int) -> str:
    # vrnetlab vIOS: eth1 → Gi0/1 … Gi0/3, then Gi1/0 … in groups of four
    # (Gi0/0 is management).
    slot, port = divmod(i + 1, 4)
    return f"GigabitEthernet{slot}/{port}"


@dataclass(frozen=True)
class DeviceKind:
    kind: str                      # containerlab kind
    label: str                     # human label
    family: str                    # iosxr | iosxe | ios | linux | eos | srl | junos | bridge
    kernel_iface: Callable[[int], str]
    config_iface: Callable[[int], str]
    mgmt_iface: str = "eth0"
    # Credentials new devices of this kind are provisioned with (see
    # topology_ops.create_node / clab_yaml). builtin_* = what the image /
    # containerlab sets up by itself when we don't override it: devices
    # created before provisioning existed (no "provision" in their
    # credentials) still log in with those.
    default_user: str = ""
    default_password: str = ""
    builtin_user: str | None = None
    builtin_password: str | None = None
    # Command to run via `docker exec` for an interactive CLI (fallback when
    # the device can't be reached over SSH, and the primary path for linux).
    exec_cli: list[str] = field(default_factory=lambda: ["sh"])
    ssh: bool = True               # device runs an SSH server on its mgmt IP
    boot_seconds: int = 30         # "running" but not ready yet for this long
    min_memory_mb: int = 512
    min_cpu_cores: int = 1
    requires_hugepages: bool = False
    is_vm: bool = False            # vrnetlab-wrapped QEMU VM (needs /dev/kvm)
    max_interfaces: int = 16
    default_interfaces: int = 4
    description: str = ""
    # Verified platform limits / quirks, shown to the lab agent (list_device_types).
    notes: str = ""
    # Ports reachable on the mgmt IP, for the UI / automation tooling.
    services: dict[str, int] = field(default_factory=dict)
    # containerlab can add/remove this kind's links while it runs. Every other
    # kind is recreated on a link change (see lab_service._recreate_plan).
    live_links: bool = False

    @property
    def builtin_credentials(self) -> tuple[str, str]:
        return (self.default_user if self.builtin_user is None else self.builtin_user,
                self.default_password if self.builtin_password is None else self.builtin_password)

    def interfaces(self, count: int) -> list[dict]:
        count = max(0, min(count, self.max_interfaces))
        return [
            {"name": self.config_iface(i), "kernel": self.kernel_iface(i)}
            for i in range(count)
        ]


_XR_SERVICES = {"ssh": 22, "netconf": 830, "gnmi": 57400}
# IOS-XE gNMI: insecure server on 50052 (`gnxi` / `gnmi-yang server`), TLS on 9339.
_XE_SERVICES = {"ssh": 22, "netconf": 830, "restconf": 443, "gnmi": 50052}

KINDS: dict[str, DeviceKind] = {k.kind: k for k in [
    DeviceKind(
        kind="cisco_xrd", label="Cisco XRd", family="iosxr",
        kernel_iface=_pattern("Gi0-0-0-{n0}"),
        config_iface=_pattern("GigabitEthernet0/0/0/{n0}"),
        mgmt_iface="MgmtEth0/RP0/CPU0/0",
        default_user="admin", default_password="cisco1", builtin_user="clab", builtin_password="clab@123",
        exec_cli=["/pkg/bin/xr_cli"],
        boot_seconds=120, min_memory_mb=2048, min_cpu_cores=2,
        max_interfaces=16, default_interfaces=4,
        description="IOS-XR as a container. Control Plane image is container-native; "
                    "the vRouter image adds a forwarding plane but needs hugepages.",
        notes="Control Plane: label-switches (LDP, L3VPN PE, EoMPLS/BGP-LU transit), PIM-SM, ACLs, LACP bundles, "
              "GR/NSF, BGP flowspec controller; needs `mpls oam` for MPLS ping. NO BFD (sessions stay down), "
              "no uRPF, input policing, CFM, G.8032 or MACsec. As an L2VPN PE only the control plane works "
              "(xconnects/VPLS PWs signal and show up, but nothing is forwarded between AC and PW) - carry "
              "L2 traffic on IOS XE PEs. One RP: NSR is config-only.",
        services={"ssh": 22, "netconf": 830, "gnmi": 9339},
    ),
    DeviceKind(
        kind="cisco_xrv9k", label="Cisco XRv 9000", family="iosxr",
        kernel_iface=_pattern("eth{n1}"),
        config_iface=_pattern("GigabitEthernet0/0/0/{n0}"),
        mgmt_iface="MgmtEth0/RP0/CPU0/0",
        default_user="admin", default_password="cisco1", builtin_user="clab", builtin_password="clab@123",
        exec_cli=["telnet", "127.0.0.1", "5000"],
        boot_seconds=600, min_memory_mb=16384, min_cpu_cores=4, is_vm=True,
        max_interfaces=16, default_interfaces=4,
        description="IOS-XR VM (vrnetlab). Full data plane: MPLS, SR, L2VPN, EVPN.",
        services=_XR_SERVICES,
    ),
    DeviceKind(
        kind="cisco_c8000v", label="Cisco Catalyst 8000v", family="iosxe",
        kernel_iface=_pattern("eth{n1}"),
        config_iface=_pattern("GigabitEthernet{n2}"),
        mgmt_iface="GigabitEthernet1",
        default_user="admin", default_password="cisco1", builtin_user="admin", builtin_password="admin",
        exec_cli=["telnet", "127.0.0.1", "5000"],
        boot_seconds=300, min_memory_mb=4096, min_cpu_cores=2, is_vm=True,
        max_interfaces=8, default_interfaces=4,
        description="IOS-XE VM (vrnetlab). NETCONF/RESTCONF/gNMI, MPLS, L3VPN.",
        notes="Like the CSR 1000v (LDP, L3VPN, EoMPLS/VPLS PE, MQC QoS, CoPP, uRPF, PIM, CFM down MEPs, BFD) "
              "but throughput is capped at 20 Mb/s unlicensed. No MACsec.",
        services=_XE_SERVICES,
    ),
    DeviceKind(
        kind="cisco_cat9kv", label="Cisco Catalyst 9000v", family="iosxe",
        kernel_iface=_pattern("eth{n1}"),
        config_iface=_pattern("GigabitEthernet1/0/{n1}"),
        mgmt_iface="GigabitEthernet0/0",
        default_user="admin", default_password="cisco1", builtin_user="admin", builtin_password="admin",
        exec_cli=["telnet", "127.0.0.1", "5000"],
        boot_seconds=720, min_memory_mb=18432, min_cpu_cores=4, is_vm=True,
        max_interfaces=24, default_interfaces=8,
        description="IOS-XE Catalyst 9000 switch VM (vrnetlab, UADP/Q200 ASIC emulation). Heavy: ~18 GB RAM.",
        notes="Switch: VLANs, SVIs, VXLAN BGP EVPN (ingress replication). Data plane comes up ~90 s after the "
              "CLI. Drops EAPoL to 01:80:c2:00:00:03 (MKA across it needs broadcast EAPoL).",
        services=_XE_SERVICES,
    ),
    DeviceKind(
        kind="cisco_csr1000v", label="Cisco CSR 1000v", family="iosxe",
        kernel_iface=_pattern("eth{n1}"),
        config_iface=_pattern("GigabitEthernet{n2}"),
        mgmt_iface="GigabitEthernet1",
        default_user="admin", default_password="cisco1", builtin_user="admin", builtin_password="admin",
        exec_cli=["telnet", "127.0.0.1", "5000"],
        boot_seconds=300, min_memory_mb=4096, min_cpu_cores=1, is_vm=True,
        max_interfaces=8, default_interfaces=4,
        description="IOS-XE VM (vrnetlab), predecessor of the Catalyst 8000v.",
        notes="IOS XE 17.3: LDP (sync, session protection, auth, label filtering), L3VPN, EoMPLS xconnect, VPLS "
              "VFI, EVC/802.1ad rewrites, CFM DOWN MEPs only (no up MEPs on service instances), 802.3ah, BFD "
              "(echo; not on Port-channels), LACP, GR/NSF helper, CoPP, uRPF, MQC (police/shape/WRED), PIM "
              "SM/SSM/BIDIR, IGMPv3/MLD, flowspec client. Capped at 1 Mb/s unlicensed. No MACsec, G.8032, PBB.",
        services=_XE_SERVICES,
    ),
    DeviceKind(
        kind="cisco_vios", label="Cisco vIOS", family="ios",
        kernel_iface=_pattern("eth{n1}"),
        config_iface=_vios_name,
        mgmt_iface="GigabitEthernet0/0",
        default_user="admin", default_password="cisco1", builtin_user="admin", builtin_password="admin",
        exec_cli=["telnet", "127.0.0.1", "5000"],
        boot_seconds=180, min_memory_mb=768, min_cpu_cores=1, is_vm=True,
        max_interfaces=15, default_interfaces=4,
        description="Classic IOS / IOS L2 switch VM (vrnetlab).",
        services={"ssh": 22},
    ),
    DeviceKind(
        kind="linux", label="Linux", family="linux",
        kernel_iface=_pattern("eth{n1}"),
        config_iface=_pattern("eth{n1}"),
        exec_cli=["sh"], ssh=False, live_links=True,
        boot_seconds=5, min_memory_mb=512, min_cpu_cores=1,
        max_interfaces=32, default_interfaces=4,
        description="Generic Linux container: FRR, hosts, the automation workstation.",
        notes="FRR Router (plabs/frr): vtysh config; startup config lines starting with '$ ' run as shell "
              "commands (kernel MACsec, wpa_supplicant MKA, sysctls). Linux Host (plabs/host): iproute2, ping, "
              "traceroute, mtr, tcpdump, iperf3, curl, dig, snmpwalk; apply_config runs shell commands and does "
              "not persist across restarts. Automation Workstation (plabs/workstation): gnmic, ncclient, "
              "netmiko, scrapli, pyATS, Ansible, pyang.",
    ),
    DeviceKind(
        kind="arista_ceos", label="Arista cEOS", family="eos",
        kernel_iface=_pattern("eth{n1}"),
        config_iface=_pattern("Ethernet{n1}"),
        mgmt_iface="Management0",
        default_user="admin", default_password="admin",
        exec_cli=["Cli"],
        boot_seconds=60, min_memory_mb=2048, min_cpu_cores=1,
        description="Arista cEOS-lab.",
        services={"ssh": 22, "gnmi": 6030},
    ),
    DeviceKind(
        kind="nokia_srlinux", label="Nokia SR Linux", family="srl",
        kernel_iface=_pattern("e1-{n1}"),
        config_iface=_pattern("ethernet-1/{n1}"),
        mgmt_iface="mgmt0",
        default_user="admin", default_password="NokiaSrl1!",
        exec_cli=["sr_cli"],
        boot_seconds=30, min_memory_mb=2048, min_cpu_cores=1,
        description="Nokia SR Linux.",
        services={"ssh": 22, "gnmi": 57400},
    ),
    DeviceKind(
        kind="juniper_crpd", label="Juniper cRPD", family="junos",
        kernel_iface=_pattern("eth{n1}"),
        config_iface=_pattern("eth{n1}"),
        default_user="root", default_password="clab123",
        exec_cli=["cli"],
        boot_seconds=15, min_memory_mb=1024, min_cpu_cores=1,
        description="Juniper cRPD (control plane only).",
        services={"ssh": 22},
    ),
]}

# Network nodes: a Linux bridge on the lab host, optionally uplinked to a
# physical NIC (access) or one VLAN on it (trunk). Not a container — its
# "interfaces" are bridge ports, created on demand as devices are cabled to it.
BRIDGE_KIND = "bridge"
KINDS[BRIDGE_KIND] = DeviceKind(
    kind=BRIDGE_KIND, label="Network", family="bridge",
    kernel_iface=_pattern("port{n1}"), config_iface=_pattern("port{n1}"),
    exec_cli=[], ssh=False, boot_seconds=0, min_memory_mb=0, min_cpu_cores=0,
    max_interfaces=128, default_interfaces=0,
    description="Connects device ports to a physical NIC, a VLAN on a trunk NIC, or an internal lab switch.",
)

DEFAULT_KIND = "linux"


def is_bridge(kind: str | None) -> bool:
    return kind == BRIDGE_KIND


def get_kind(kind: str | None) -> DeviceKind:
    return KINDS.get(kind or "", KINDS[DEFAULT_KIND])


def is_known_kind(kind: str | None) -> bool:
    return bool(kind) and kind in KINDS


def kind_catalog() -> list[dict]:
    """Serializable registry for the frontend (template editor, palette)."""
    return [
        {
            "kind": k.kind, "label": k.label, "family": k.family,
            "mgmt_iface": k.mgmt_iface, "default_user": k.default_user,
            "ssh": k.ssh, "boot_seconds": k.boot_seconds,
            "min_memory_mb": k.min_memory_mb, "min_cpu_cores": k.min_cpu_cores,
            "requires_hugepages": k.requires_hugepages, "is_vm": k.is_vm,
            "max_interfaces": k.max_interfaces, "default_interfaces": k.default_interfaces,
            "description": k.description, "notes": k.notes, "services": dict(k.services),
            "example_interfaces": k.interfaces(3),
        }
        for k in KINDS.values()
    ]
