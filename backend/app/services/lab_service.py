"""Containerlab lab orchestration service.

Wraps the `containerlab` CLI to deploy/destroy/inspect labs derived from Phanero
topologies. Also handles loading vendor container images from the
device_images/ directory into the host Docker daemon.

All `containerlab` and `docker` invocations happen inside the backend container,
which mounts the host Docker socket — so spawned containers actually live on
the host.
"""

import json
import os
import subprocess
import tarfile
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Optional
from sqlalchemy.orm import Session

from app.models.lab_deployment import LabDeployment
from app.services.clab_yaml import generate_clab_yaml


LAB_STATE_DIR = os.environ.get("LAB_STATE_DIR", "/app/lab_state")
DEVICE_IMAGES_DIR = os.environ.get("DEVICE_IMAGES_DIR", "/app/device_images")


class LabError(Exception):
    """Lab orchestration failure with a user-facing message."""


class LabBusyError(LabError):
    """Another topology already has a lab deployed (only one lab runs at a time).
    Carries the occupying topology's id so the API can report it (as a 409)."""
    def __init__(self, other_topology_id: int, other_topology_name: str = ""):
        self.other_topology_id = other_topology_id
        self.other_topology_name = other_topology_name
        name = f" ({other_topology_name})" if other_topology_name else ""
        super().__init__(
            f"Topology {other_topology_id}{name} already has a running lab — "
            f"only one lab can run at a time. Destroy it first."
        )


def _assert_no_other_lab_running(db: Session, topology_id: int) -> None:
    """Enforce a single active lab across all topologies. Raises LabBusyError if
    a DIFFERENT topology holds the slot (deploying/running/destroying)."""
    other = (
        db.query(LabDeployment)
        .filter(
            LabDeployment.topology_id != topology_id,
            LabDeployment.status.in_(["deploying", "running", "destroying"]),
        )
        .first()
    )
    if other:
        from app.models.topology import Topology
        topo = db.query(Topology).filter(Topology.id == other.topology_id).first()
        raise LabBusyError(other.topology_id, topo.name if topo else "")


def load_gnmi_subscriptions(db: Session, topology_id: int) -> list[dict]:
    """Enabled user-defined gNMI subscriptions for a topology, as gnmic-config
    dicts (name/path/mode/sample_interval) for build_gnmic_config."""
    from app.models.lab_gnmi_subscription import LabGnmiSubscription
    rows = (
        db.query(LabGnmiSubscription)
        .filter(LabGnmiSubscription.topology_id == topology_id,
                LabGnmiSubscription.enabled.is_(True))
        .all()
    )
    return [{"name": r.name, "path": r.path, "mode": r.mode,
             "sample_interval": r.sample_interval} for r in rows]


def _lab_dir_for(lab_name: str) -> Path:
    p = Path(LAB_STATE_DIR) / lab_name
    p.mkdir(parents=True, exist_ok=True)
    return p


def _yaml_path_for(lab_name: str) -> Path:
    return _lab_dir_for(lab_name) / "topology.clab.yml"


def _run(cmd: list[str], timeout: int = 600, cwd: Optional[str] = None) -> tuple[int, str, str]:
    """Run a command, return (rc, stdout, stderr). Never raises on non-zero rc."""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        return 124, "", f"command timed out after {timeout}s: {' '.join(cmd)}"


def _get_or_create_deployment(db: Session, topology_id: int) -> LabDeployment:
    dep = db.query(LabDeployment).filter(LabDeployment.topology_id == topology_id).first()
    if not dep:
        dep = LabDeployment(topology_id=topology_id, status="idle")
        db.add(dep)
        db.commit()
        db.refresh(dep)
    return dep


def _read_int(path: str) -> Optional[int]:
    try:
        with open(path) as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def _kernel_module_available(name: str) -> bool:
    """True if a kernel module is loaded or built-in. Kernel modules are
    global, so the backend container's /sys and /proc reflect the host kernel
    that also runs the lab containers. Covers built-in modules (which still get
    a /sys/module/<name> dir) and loaded ones (listed in /proc/modules)."""
    if os.path.isdir(f"/sys/module/{name}"):
        return True
    try:
        with open("/proc/modules") as f:
            return any(line.split(" ", 1)[0] == name for line in f)
    except OSError:
        return False


# Per-manager install prefix, used to tailor the copy-paste prereq fixes.
_PKG_INSTALL = {
    "apt": "sudo apt install -y",
    "dnf": "sudo dnf install -y",
    "yum": "sudo yum install -y",
    "zypper": "sudo zypper install -y",
}


def _host_pkg_manager() -> Optional[str]:
    """Best-effort detection of the HOST distro's package manager, so the prereq
    fix commands match the box the user will run them on. The backend is a
    Debian-based container, so probing our OWN filesystem for `apt` would always
    say Debian — instead read the host's os-release (bind-mounted read-only at
    /host/os-release) and, failing that, the host kernel's release string (which
    needs no mount). Returns 'apt' | 'dnf' | 'yum' | 'zypper' | None (unknown)."""
    ids = ""
    try:
        data: dict[str, str] = {}
        with open("/host/os-release") as f:
            for line in f:
                if "=" in line:
                    k, v = line.rstrip().split("=", 1)
                    data[k.strip().lower()] = v.strip().strip('"').lower()
        ids = f"{data.get('id', '')} {data.get('id_like', '')}"
    except OSError:
        ids = ""
    if any(x in ids for x in ("rhel", "fedora", "centos", "rocky", "almalinux", "amzn")):
        return "dnf"
    if any(x in ids for x in ("debian", "ubuntu")):
        return "apt"
    if "suse" in ids:
        return "zypper"
    # Fallback: the host kernel release string (readable in-container, no mount).
    rel = ""
    try:
        with open("/proc/sys/kernel/osrelease") as f:
            rel = f.read().strip().lower()
    except OSError:
        pass
    if any(x in rel for x in ("amzn", ".el", ".fc")):
        return "dnf"
    if any(x in rel for x in ("-generic", "-aws", "-azure", "-gcp", "-cloud")):
        return "apt"
    return None


def _ksm_fix_cmd(pkg: Optional[str]) -> str:
    """KSM enable commands: transient sysfs writes now + reboot persistence. The
    persistence uses a systemd-tmpfiles drop-in (works on every distro, no
    package); ksmtuned is offered as an optional adaptive alternative, with the
    install line matched to the detected package manager."""
    now = (
        "# Apply now (this boot):\n"
        "sudo sh -c 'echo 1    > /sys/kernel/mm/ksm/run'\n"
        "sudo sh -c 'echo 1000 > /sys/kernel/mm/ksm/pages_to_scan'\n"
        "sudo sh -c 'echo 200  > /sys/kernel/mm/ksm/sleep_millisecs'"
    )
    persist = (
        "# Persist across reboots (distro-agnostic, no package needed):\n"
        "sudo tee /etc/tmpfiles.d/ksm.conf >/dev/null <<'EOF'\n"
        "w /sys/kernel/mm/ksm/run             - - - - 1\n"
        "w /sys/kernel/mm/ksm/pages_to_scan   - - - - 1000\n"
        "w /sys/kernel/mm/ksm/sleep_millisecs - - - - 200\n"
        "EOF"
    )
    mgr = _PKG_INSTALL.get(pkg or "")
    if mgr:
        # RHEL-family ships two units (ksm enables, ksmtuned tunes); others one.
        units = "ksm ksmtuned" if pkg in ("dnf", "yum", "zypper") else "ksmtuned"
        opt = (
            "# Optional adaptive daemon instead of the tmpfiles drop-in:\n"
            f"#   {mgr} ksmtuned && sudo systemctl enable --now {units}"
        )
    else:
        opt = (
            "# Optional adaptive daemon: install 'ksmtuned' and enable it\n"
            "#   RHEL/Fedora: sudo dnf install -y ksmtuned && sudo systemctl enable --now ksm ksmtuned\n"
            "#   Debian/Ubuntu: sudo apt install -y ksmtuned && sudo systemctl enable --now ksmtuned"
        )
    return f"{now}\n{persist}\n{opt}"


def _zram_fix_cmd(pkg: Optional[str]) -> str:
    """zram compressed-swap setup. Both flavors install a systemd unit, so they
    apply now AND persist across reboots. Debian/Ubuntu use zram-tools; the rest
    use systemd's zram-generator. Unknown host distro → show both."""
    apt = (
        "# Debian/Ubuntu (applies now + persists):\n"
        "sudo apt install -y zram-tools\n"
        "sudo sh -c 'printf \"ALGO=zstd\\nPERCENT=50\\n\" > /etc/default/zramswap'\n"
        "sudo systemctl restart zramswap"
    )

    def generator(install: str) -> str:
        return (
            "# RHEL/Fedora/SUSE (applies now + persists):\n"
            f"{install} zram-generator\n"
            "sudo tee /etc/systemd/zram-generator.conf >/dev/null <<'EOF'\n"
            "[zram0]\n"
            "zram-size = ram / 2\n"
            "compression-algorithm = zstd\n"
            "EOF\n"
            "sudo systemctl daemon-reload\n"
            "sudo systemctl start systemd-zram-setup@zram0.service"
        )

    if pkg == "apt":
        return apt
    if pkg in ("dnf", "yum", "zypper"):
        return generator(_PKG_INSTALL[pkg])
    return apt + "\n\n" + generator("sudo dnf install -y")


def get_host_prereqs(db: Session, topology_id: Optional[int] = None) -> dict:
    """Check the host's kernel-level prerequisites for the topology we'd
    deploy. Returns each check with current value, required minimum, ok flag,
    and a fix command the user can copy/paste on the host.

    If topology_id is provided, sizes (hugepages, inotify) scale to the number
    of XRd-class nodes the lab view would produce — otherwise reasonable
    baseline defaults are reported.
    """
    pkg = _host_pkg_manager()  # tailor copy-paste fixes to the host's distro
    xrd_count = 0
    total_nodes = 0
    if topology_id is not None:
        try:
            from app.services.lab_view import compute_lab_view
            view = compute_lab_view(db, topology_id)
            total_nodes = len(view.get("nodes", []))
            for n in view.get("nodes", []):
                if n.get("lab_kind") in ("cisco_xrd", "cisco_xrv9k"):
                    xrd_count += 1
        except Exception:
            xrd_count = 0

    # Sizes — XRd Control Plane's documented recommendations:
    #   ~4000 inotify instances/watches per node. Hugepages are NOT required
    #   for the Control Plane variant (the vRouter needed them, but vRouter
    #   isn't supported by containerlab).
    needed_inotify_instances = max(64000, 4000 * xrd_count) if xrd_count else 0
    needed_inotify_watches = max(1048576, 4000 * xrd_count) if xrd_count else 0
    needed_hugepages = 0

    checks: list[dict] = []

    inst = _read_int("/proc/sys/fs/inotify/max_user_instances")
    checks.append({
        "id": "inotify_instances",
        "label": "fs.inotify.max_user_instances",
        "value": inst,
        "required_min": needed_inotify_instances,
        "ok": (needed_inotify_instances == 0) or (inst is not None and inst >= needed_inotify_instances),
        "fix_command": "sudo sysctl -w fs.inotify.max_user_instances=64000",
        # 128 is the kernel default; some distros raise it slightly.
        "revert_command": "sudo sysctl -w fs.inotify.max_user_instances=128",
        "reason": "XRd needs ~4000 inotify instances per node",
    })

    watches = _read_int("/proc/sys/fs/inotify/max_user_watches")
    checks.append({
        "id": "inotify_watches",
        "label": "fs.inotify.max_user_watches",
        "value": watches,
        "required_min": needed_inotify_watches,
        "ok": (needed_inotify_watches == 0) or (watches is not None and watches >= needed_inotify_watches),
        "fix_command": "sudo sysctl -w fs.inotify.max_user_watches=1048576",
        # 8192 is the canonical kernel default; Ubuntu ships 65536.
        "revert_command": "sudo sysctl -w fs.inotify.max_user_watches=8192",
        "reason": "XRd needs many inotify watches",
    })

    hp = _read_int("/proc/sys/vm/nr_hugepages")
    hugepages_target = max(needed_hugepages, 3000)
    # Hugepages require *contiguous* free memory. On a host that's been up a
    # while, fragmentation often blocks `sysctl -w` from reaching the target —
    # the kernel allocates what it can and silently drops the rest. Dropping
    # caches first frees contiguous memory, so we prepend that.
    hugepages_fix = (
        "sudo sync\n"
        "sudo sysctl -w vm.drop_caches=3\n"
        f"sudo sysctl -w vm.nr_hugepages={hugepages_target}\n"
        "# If `cat /proc/sys/vm/nr_hugepages` still shows < target, the host\n"
        "# is too fragmented to allocate them now — reserve at boot instead:\n"
        "#   1. sudo nano /etc/default/grub\n"
        f"#   2. add to GRUB_CMDLINE_LINUX:  hugepages={hugepages_target} default_hugepagesz=2M\n"
        "#   3. sudo update-grub && sudo reboot"
    )
    hugepages_revert = (
        "sudo sysctl -w vm.nr_hugepages=0\n"
        "# Note: this only succeeds if no process currently has hugepages mapped.\n"
        "# If you also set hugepages=N in GRUB, edit /etc/default/grub to remove\n"
        "# that token and run `sudo update-grub && sudo reboot`."
    )
    checks.append({
        "id": "hugepages",
        "label": "vm.nr_hugepages",
        "value": hp,
        "required_min": needed_hugepages,
        "ok": (needed_hugepages == 0) or (hp is not None and hp >= needed_hugepages),
        "fix_command": hugepages_fix,
        "revert_command": hugepages_revert,
        "reason": "Not required — XRd Control Plane doesn't use hugepages, and the vRouter variant isn't supported by containerlab",
    })

    # KSM (Kernel Samepage Merging) — the kernel scans process memory and
    # deduplicates identical pages across containers/VMs. Pure container labs
    # see modest gains; VM-based (vrnetlab) labs with multiple copies of the
    # same image can see 40-70% memory savings. Off by default on most distros.
    # We flag it whenever there's more than one node (no point deduping a
    # single-node lab).
    ksm_run = _read_int("/sys/kernel/mm/ksm/run")
    ksm_pages = _read_int("/sys/kernel/mm/ksm/pages_to_scan")
    ksm_sleep = _read_int("/sys/kernel/mm/ksm/sleep_millisecs")
    ksm_present = ksm_run is not None
    # Not fixable on a host whose kernel lacks KSM, so don't flag it as a
    # failure there — only require it when the kernel actually supports it.
    ksm_required = 1 if (total_nodes > 1 and ksm_present) else 0
    ksm_fix = _ksm_fix_cmd(pkg)
    if ksm_run is None:
        ksm_reason = ("KSM not compiled into this kernel (CONFIG_KSM=n) — can't be "
                      "enabled here; skip it (it's an optional optimization)")
    elif total_nodes <= 1:
        ksm_reason = "Only meaningful with multiple nodes — nothing to dedupe"
    else:
        # Build a short note about the current tuning when KSM is on
        tuning_bits = []
        if ksm_pages is not None and ksm_pages < 500:
            tuning_bits.append(f"pages_to_scan={ksm_pages} (try 1000+ for faster dedup)")
        if ksm_sleep is not None and ksm_sleep > 500:
            tuning_bits.append(f"sleep_millisecs={ksm_sleep} (try 200 for faster dedup)")
        ksm_reason = (
            f"Deduplicates memory across {total_nodes} lab nodes — biggest win with identical images"
            + (". Tuning: " + ", ".join(tuning_bits) if tuning_bits else "")
        )
    ksm_revert = (
        "sudo sh -c 'echo 0 > /sys/kernel/mm/ksm/run'\n"
        "# Optional — actively un-merge pages instead of just stopping further dedup:\n"
        "#   sudo sh -c 'echo 2 > /sys/kernel/mm/ksm/run'\n"
        "# Remove persistence (only the line that matches what you installed):\n"
        "sudo systemctl disable --now ksmtuned 2>/dev/null || true\n"
        "sudo rm -f /etc/tmpfiles.d/ksm.conf"
    )
    checks.append({
        "id": "ksm",
        "label": "Kernel Samepage Merging (KSM)",
        "value": ksm_run,
        "required_min": ksm_required,
        "ok": (ksm_required == 0) or (ksm_run is not None and ksm_run >= 1),
        "fix_command": ksm_fix,
        "revert_command": ksm_revert,
        "reason": ksm_reason,
    })

    # vm.overcommit_memory=1 — lets the kernel hand out more "claimed" memory
    # than it physically has. VMs/containers rarely touch their full RAM
    # ceiling at the same time, so overcommit lets you pack significantly more
    # nodes on the same host. 0 (heuristic, default) often blocks fork()
    # for big-memory processes under pressure; 1 (always overcommit) is the
    # standard hypervisor setting.
    overcommit = _read_int("/proc/sys/vm/overcommit_memory")
    overcommit_required = 1 if total_nodes > 1 else 0
    checks.append({
        "id": "overcommit_memory",
        "label": "vm.overcommit_memory",
        "value": overcommit,
        "required_min": overcommit_required,
        # 1 = "always overcommit". 2 (strict accounting) defeats the purpose.
        "ok": (overcommit_required == 0) or (overcommit == 1),
        "fix_command": "sudo sysctl -w vm.overcommit_memory=1",
        # 0 = heuristic, the kernel default.
        "revert_command": "sudo sysctl -w vm.overcommit_memory=0",
        "reason": (
            f"Allow VMs/containers to over-claim RAM — essential when packing "
            f"{total_nodes} memory-heavy nodes on one host"
            if total_nodes > 1
            else "Optimization for multi-node labs (off when only one node)"
        ),
    })

    # zram — compressed in-RAM swap. Idle/cold VM memory pages get compressed
    # (typically ~3x with zstd) instead of being written to disk. On low-RAM
    # hosts this is the difference between "lab boots" and "OOM-kill".
    # Detect by scanning /proc/swaps for any zram device.
    zram_active = False
    zram_size_kb = 0
    try:
        with open("/proc/swaps") as f:
            for line in f.readlines()[1:]:  # skip header
                parts = line.split()
                if parts and "zram" in parts[0]:
                    zram_active = True
                    if len(parts) >= 3:
                        try:
                            zram_size_kb += int(parts[2])
                        except ValueError:
                            pass
    except OSError:
        pass
    zram_size_mb = zram_size_kb // 1024
    zram_required = 1 if total_nodes > 1 else 0
    zram_fix = _zram_fix_cmd(pkg)
    if zram_active:
        zram_reason = (
            f"{zram_size_mb}MB zram active — at ~3x compression, "
            f"this absorbs ~{zram_size_mb * 3}MB of cold VM/container pages"
        )
    elif total_nodes > 1:
        zram_reason = "Compressed in-RAM swap soaks up idle VM/container pages — big win on low-RAM hosts"
    else:
        zram_reason = "Optimization for multi-node labs (off when only one node)"
    zram_revert = (
        "# Debian/Ubuntu (zram-tools):\n"
        "sudo systemctl disable --now zramswap 2>/dev/null || true\n"
        "# Keep the package in case you want it back; uncomment to fully remove:\n"
        "#   sudo apt remove -y zram-tools\n"
        "\n"
        "# Fedora / Arch (zram-generator):\n"
        "#   sudo systemctl stop systemd-zram-setup@zram0.service\n"
        "#   sudo rm /etc/systemd/zram-generator.conf\n"
        "#   sudo systemctl daemon-reload"
    )
    checks.append({
        "id": "zram",
        "label": "zram compressed swap",
        # Use 1/0 so it slots into the existing value/required_min display.
        "value": 1 if zram_active else 0,
        "required_min": zram_required,
        "ok": (zram_required == 0) or zram_active,
        "fix_command": zram_fix,
        "revert_command": zram_revert,
        "reason": zram_reason,
    })

    # sch_netem — the kernel qdisc module behind link degradation
    # (`containerlab tools netem`, exposed via the lab-mode "Link degradation"
    # menu). containerlab's container can't load it itself, so it must be
    # present on the host. Only meaningful once there's a link to impair
    # (>= 2 nodes). Detected via the shared host kernel.
    netem_ok = _kernel_module_available("sch_netem")
    netem_required = 1 if total_nodes > 1 else 0
    netem_fix = (
        "sudo modprobe sch_netem\n"
        "# Persist across reboots:\n"
        "sudo sh -c 'echo sch_netem > /etc/modules-load.d/sch_netem.conf'"
    )
    netem_revert = (
        "# Only removable when no link currently has an impairment attached:\n"
        "sudo modprobe -r sch_netem 2>/dev/null || true\n"
        "sudo rm -f /etc/modules-load.d/sch_netem.conf"
    )
    checks.append({
        "id": "sch_netem",
        "label": "sch_netem kernel module (link degradation)",
        "value": 1 if netem_ok else 0,
        "required_min": netem_required,
        "ok": (netem_required == 0) or netem_ok,
        "fix_command": netem_fix,
        "revert_command": netem_revert,
        "reason": "Required for lab link degradation — apply latency/jitter/loss/rate to links via containerlab netem",
    })

    # Build a one-shot fix snippet (transient + persistent)
    failing = [c for c in checks if not c["ok"]]
    transient = "\n".join(c["fix_command"] for c in failing)
    persistent = ""
    if failing:
        # Convert any `sudo sysctl -w KEY=VALUE` lines (and only those) into
        # KEY=VALUE entries for /etc/sysctl.d. Skip multi-line fix commands.
        # Some sysctls are one-shot triggers (e.g. vm.drop_caches) and have no
        # business being persisted — those are filtered out here.
        TRANSIENT_ONLY = {"vm.drop_caches"}
        lines = []
        for c in failing:
            for line in c["fix_command"].splitlines():
                line = line.strip()
                if line.startswith("sudo sysctl -w "):
                    kv = line.split(" -w ", 1)[1].strip()
                    key = kv.split("=", 1)[0]
                    if key in TRANSIENT_ONLY:
                        continue
                    lines.append(kv)
        if lines:
            body = "\n".join(lines)
            persistent = (
                "sudo tee /etc/sysctl.d/99-phanero-lab.conf >/dev/null <<'EOF'\n"
                f"{body}\n"
                "EOF\n"
                "sudo sysctl -p /etc/sysctl.d/99-phanero-lab.conf\n"
                "# Note: persistent sysctls re-apply at boot but hugepages can\n"
                "# still fail to allocate on a fragmented host. For guaranteed\n"
                "# hugepage availability, see the boot-param option in the\n"
                "# vm.nr_hugepages fix above."
            )

    # Revert snippets — always built (independent of `failing`) so the user can
    # roll back even after everything has been applied successfully.
    revert_transient = "\n".join(c["revert_command"] for c in checks if c.get("revert_command"))
    # The persistent-revert undoes the apply-side artifacts: the auto-generated
    # sysctl.d file plus any per-check files (ksm tmpfiles, zram service).
    # Wrapped in `|| true` so missing files don't break the script.
    revert_persistent = (
        "# Remove the auto-generated sysctl drop-in:\n"
        "sudo rm -f /etc/sysctl.d/99-phanero-lab.conf\n"
        "# Reload sysctls so defaults from /etc/sysctl.conf re-apply:\n"
        "sudo sysctl --system >/dev/null\n"
        "\n"
        "# Then run the per-check revert commands above to reset live values\n"
        "# and remove any KSM / zram persistence drop-ins.\n"
        "\n"
        "# If you added `hugepages=N` to GRUB, edit /etc/default/grub to remove\n"
        "# that token, then `sudo update-grub && sudo reboot`."
    )

    return {
        "xrd_node_count": xrd_count,
        "checks": checks,
        "all_ok": not failing,
        "fix_snippet_transient": transient,
        "fix_snippet_persistent": persistent,
        "revert_snippet_transient": revert_transient,
        "revert_snippet_persistent": revert_persistent,
    }


def list_device_images() -> list[dict]:
    """List image files in DEVICE_IMAGES_DIR with metadata about whether each
    has been loaded into the host Docker daemon."""
    images_dir = Path(DEVICE_IMAGES_DIR)
    if not images_dir.exists():
        return []

    # Get loaded image refs from the host docker daemon
    rc, out, _err = _run(["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"], timeout=15)
    loaded_refs = set(out.strip().splitlines()) if rc == 0 else set()

    items = []
    for f in sorted(images_dir.iterdir()):
        if not f.is_file():
            continue
        size_mb = f.stat().st_size // (1024 * 1024)
        items.append({
            "filename": f.name,
            "path": str(f),
            "size_mb": size_mb,
            # We can't tell from filename alone what ref a .tgz will load as.
            "loaded_image_refs": [],  # populated below for files we can introspect
        })

    return [
        {**i, "host_loaded_refs": sorted(loaded_refs)}
        for i in items
    ]


def _find_nested_docker_archive(tar_path: Path) -> Optional[str]:
    """Detect a Cisco-style signed-release bundle and return the inner
    docker-archive member name. None means the outer file IS the docker archive.

    Heuristic: if the archive's top level contains a single nested .tgz/.tar
    alongside signature/cert files (Cisco IOS-XR / XRd release pattern),
    that's the real docker image — unwrap it.
    """
    try:
        with tarfile.open(tar_path, "r:*") as tf:
            members = [m for m in tf.getnames() if m and not m.startswith(".")]
    except (tarfile.ReadError, OSError):
        return None

    # If the outer archive itself looks like a docker save (contains manifest.json
    # or a "repositories" file at the root), no unwrapping needed.
    root_files = {m for m in members if "/" not in m}
    if "manifest.json" in root_files or "repositories" in root_files or "index.json" in root_files:
        return None

    # Look for a Cisco-style inner archive. Prefer the .dockerv1.tgz pattern;
    # fall back to any single .tgz/.tar.gz/.tar in the bundle.
    candidates: list[str] = []
    for m in members:
        lower = m.lower()
        if lower.endswith(".signature") or lower.endswith(".sig"):
            continue
        if lower.endswith(".tgz") or lower.endswith(".tar.gz") or lower.endswith(".tar"):
            candidates.append(m)

    # Prefer one with "docker" in the name (Cisco's pattern)
    docker_named = [c for c in candidates if "docker" in c.lower()]
    if docker_named:
        return docker_named[0]
    if len(candidates) == 1:
        return candidates[0]
    return None


def _extract_member_to_tempfile(tar_path: Path, member: str) -> Path:
    """Stream a named member out of an outer tarball into a temp file. Returns
    the temp file path. Caller is responsible for unlinking."""
    suffix = "." + member.split("/")[-1]
    fd, tmp_name = tempfile.mkstemp(suffix=suffix, prefix="phanero-clab-")
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        with tarfile.open(tar_path, "r:*") as tf:
            extracted = tf.extractfile(member)
            if extracted is None:
                raise LabError(f"Inner archive '{member}' could not be extracted from {tar_path.name}")
            with open(tmp_path, "wb") as out:
                while True:
                    chunk = extracted.read(1024 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
    except Exception:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise
    return tmp_path


def save_uploaded_image(upload_filename: str, file_obj) -> dict:
    """Stream an uploaded image file into DEVICE_IMAGES_DIR.

    The browser POSTs multipart/form-data; FastAPI hands us an UploadFile-like
    object whose .file is a SpooledTemporaryFile. We copy it to the bind-mount
    in chunks so even multi-GiB vendor archives don't get held in memory.
    """
    if not upload_filename or "/" in upload_filename or "\\" in upload_filename or upload_filename in (".", ".."):
        raise LabError("Invalid filename")

    images_dir = Path(DEVICE_IMAGES_DIR)
    images_dir.mkdir(parents=True, exist_ok=True)
    dest = images_dir / upload_filename
    if dest.resolve().parent != images_dir.resolve():
        raise LabError("Upload target outside DEVICE_IMAGES_DIR is not allowed")

    bytes_written = 0
    # Write to a temp name first so a half-uploaded file doesn't masquerade
    # as a complete one if the connection drops.
    tmp_dest = images_dir / f".{upload_filename}.partial"
    try:
        with open(tmp_dest, "wb") as out:
            while True:
                chunk = file_obj.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                bytes_written += len(chunk)
        os.replace(tmp_dest, dest)
    except Exception:
        try:
            tmp_dest.unlink()
        except OSError:
            pass
        raise

    return {
        "filename": upload_filename,
        "size_mb": bytes_written // (1024 * 1024),
        "path": str(dest),
    }


def load_device_image(filename: str) -> dict:
    """Run `docker load -i <file>` for an image file in DEVICE_IMAGES_DIR.

    Some vendors (notably Cisco for XRd) ship the image inside a signed-release
    bundle — an outer tarball that contains the actual docker archive plus
    signature files. We detect that case and load the inner archive directly.

    Returns the loaded image refs as reported by Docker so the user can paste
    them into a node template's image_ref field.
    """
    images_dir = Path(DEVICE_IMAGES_DIR)
    full = images_dir / filename
    if not full.exists() or not full.is_file():
        raise LabError(f"Image file not found: {filename}")
    if images_dir.resolve() not in full.resolve().parents:
        raise LabError("Image path outside DEVICE_IMAGES_DIR is not allowed")

    notes: list[str] = []
    target_path = full
    temp_extracted: Optional[Path] = None

    inner = _find_nested_docker_archive(full)
    if inner:
        notes.append(f"Detected signed-release bundle; unwrapping inner archive '{inner}' before loading.")
        temp_extracted = _extract_member_to_tempfile(full, inner)
        target_path = temp_extracted

    try:
        rc, out, err = _run(["docker", "load", "-i", str(target_path)], timeout=900)
    finally:
        if temp_extracted and temp_extracted.exists():
            try:
                temp_extracted.unlink()
            except OSError:
                pass

    if rc != 0:
        raise LabError(f"docker load failed: {err or out}")

    # Parse loaded refs from output, e.g. "Loaded image: ios-xr/xrd-vrouter:25.4.2"
    loaded = []
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("Loaded image:"):
            loaded.append(line.split(":", 1)[1].strip())
        elif line.startswith("Loaded image ID:"):
            loaded.append(line.split(":", 1)[1].strip())

    return {"filename": filename, "loaded_refs": loaded, "raw_output": out, "notes": notes}


# Per-kind "running but still booting" threshold. Container shows state=running
# but the platform inside isn't ready to take CLI/SSH for this many seconds.
# After this elapses we consider the node "up" rather than "booting".
_KIND_BOOT_THRESHOLD_SEC: dict[str, int] = {
    "linux": 5,
    "cisco_xrd": 120,
    "cisco_xrv9k": 240,
    "arista_ceos": 60,
    "nokia_srlinux": 30,
    "juniper_crpd": 15,
}
_DEFAULT_BOOT_THRESHOLD_SEC = 30


def _normalize_inspect_payload(data, lab_name: str) -> list[dict]:
    """Coerce `containerlab inspect --format json` output into a flat list of
    container entries, regardless of which clab major reformatted it.

    Known shapes:
      - 0.55 era: {"containers": [ {...}, {...} ]}
      - even older / some commands: [ {...}, {...} ]
      - 0.75+:    {"<lab_name>": [ {...}, {...} ]}  (key is the lab name)

    When the top-level dict has multiple lab keys (e.g. --all without a
    --name filter), we prefer the one matching `lab_name`; if no match we
    fall back to concatenating all values so the caller still sees
    *something* rather than an empty list.
    """
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    if "containers" in data and isinstance(data["containers"], list):
        return data["containers"]
    # 0.75+ shape: dict keyed by lab name. The values are lists of containers.
    if lab_name and lab_name in data and isinstance(data[lab_name], list):
        return data[lab_name]
    # Last resort: gather every list-typed value. Useful when inspect was
    # called without --name and clab returned multiple labs.
    out: list[dict] = []
    for v in data.values():
        if isinstance(v, list):
            out.extend(v)
    return out


def _strip_cidr(ip: str) -> str:
    """clab 0.75 reports mgmt IPs with a CIDR suffix ('172.20.20.2/24');
    earlier versions returned the bare address. The UI / SSH-link builders
    want the address only — strip the suffix uniformly."""
    if not ip or ip in ("N/A", "n/a"):
        return ""
    return ip.split("/", 1)[0]


def _enrich_container(c: dict) -> dict:
    """Add docker-inspect-derived fields (started_at, uptime_sec, mgmt_ip)
    and a high-level ready_state to a containerlab inspect entry."""
    cid = c.get("container_id") or c.get("ContainerID") or ""
    kind = c.get("kind") or "linux"
    state = (c.get("state") or "").lower()

    uptime_sec = None
    started_at_iso = None
    # clab 0.75+ returns mgmt IPs with a CIDR suffix; older versions returned
    # the bare address. _strip_cidr normalises both forms.
    mgmt_ip = _strip_cidr(c.get("ipv4_address") or "")

    if cid:
        rc, out, _err = _run(
            ["docker", "inspect", "--format",
             "{{.State.StartedAt}}\t{{range $k,$v := .NetworkSettings.Networks}}{{$k}}={{$v.IPAddress}};{{end}}",
             cid],
            timeout=10,
        )
        if rc == 0 and out.strip():
            try:
                line = out.strip()
                started_at_str, networks_str = line.split("\t", 1) if "\t" in line else (line, "")
                # StartedAt is "0001-01-01T00:00:00Z" before first run; treat as None
                if started_at_str and not started_at_str.startswith("0001-"):
                    started_at_iso = started_at_str
                    try:
                        from datetime import datetime, timezone
                        # Docker emits RFC3339; tolerate fractional seconds
                        s = started_at_str.replace("Z", "+00:00")
                        # Truncate sub-microsecond precision if present
                        if "." in s:
                            head, rest = s.split(".", 1)
                            tz_idx = max(rest.find("+"), rest.find("-"))
                            tz_part = rest[tz_idx:] if tz_idx >= 0 else ""
                            frac = rest[:tz_idx] if tz_idx >= 0 else rest
                            frac = frac[:6]  # microseconds max
                            s = f"{head}.{frac}{tz_part}"
                        dt = datetime.fromisoformat(s)
                        now = datetime.now(timezone.utc)
                        uptime_sec = int((now - dt).total_seconds())
                    except Exception:
                        pass
                # If clab didn't give us an IPv4, pluck one from network settings
                if not mgmt_ip and networks_str:
                    for entry in networks_str.split(";"):
                        if "=" not in entry:
                            continue
                        net_name, ip = entry.split("=", 1)
                        if ip and net_name.lower() in ("clab", "mgmt", "bridge") or (ip and not mgmt_ip):
                            mgmt_ip = ip
                            if net_name.lower() == "clab":
                                break
            except Exception:
                pass

    # Derive ready_state
    if state == "running":
        threshold = _KIND_BOOT_THRESHOLD_SEC.get(kind, _DEFAULT_BOOT_THRESHOLD_SEC)
        if uptime_sec is None or uptime_sec < threshold:
            ready_state = "booting"
        else:
            ready_state = "up"
    elif state in ("exited", "dead"):
        ready_state = "down"
    elif state in ("created", "restarting", "paused"):
        ready_state = "starting"
    else:
        ready_state = "unknown"

    return {
        **c,
        "started_at": started_at_iso,
        "uptime_sec": uptime_sec,
        "mgmt_ip": mgmt_ip,
        "ready_state": ready_state,
        "boot_threshold_sec": _KIND_BOOT_THRESHOLD_SEC.get(kind, _DEFAULT_BOOT_THRESHOLD_SEC),
    }


def get_deployment_state(db: Session, topology_id: int) -> dict:
    """Return the recorded state for a lab plus a fresh `containerlab inspect`
    against any live containers, enriched with uptime / ready_state / mgmt IP."""
    dep = _get_or_create_deployment(db, topology_id)
    live = []
    if dep.lab_name:
        rc, out, _err = _run(
            ["containerlab", "inspect", "--name", dep.lab_name, "--format", "json"],
            timeout=15,
        )
        if rc == 0 and out.strip():
            try:
                data = json.loads(out)
                live = _normalize_inspect_payload(data, dep.lab_name)
            except Exception:
                live = []
    enriched = [_enrich_container(c) for c in live]
    return {
        "status": dep.status,
        "lab_name": dep.lab_name,
        "lab_dir": dep.lab_dir,
        "last_error": dep.last_error,
        "started_at": dep.started_at.isoformat() if dep.started_at else None,
        "finished_at": dep.finished_at.isoformat() if dep.finished_at else None,
        "containers": enriched,
    }


def _ensure_fresh_yaml(db: Session, topology_id: int) -> tuple[Path, str, dict]:
    """(Re)generate the clab YAML for a topology and write it to disk.

    Used by every operation that touches the on-disk YAML so that per-node
    actions (Start/Stop/Restart) always see template changes — without
    forcing the user to click Deploy after every edit. The deploy itself
    also routes through here so there's one canonical "write the YAML"
    code path.

    Returns (yaml_path, lab_name, spec). Updates the LabDeployment row's
    lab_name/lab_dir/last_yaml so subsequent reads agree with disk.
    Raises LabError if the topology has nothing deployable.
    """
    spec = generate_clab_yaml(db, topology_id)
    if not spec["included_nodes"]:
        raise LabError("Topology has no deployable nodes (set clab_kind + image_ref on the templates first).")
    lab_name = spec["lab_name"]
    yaml_path = _yaml_path_for(lab_name)
    yaml_path.write_text(spec["yaml"])

    dep = _get_or_create_deployment(db, topology_id)
    if dep.lab_name != lab_name:
        dep.lab_name = lab_name
    dep.lab_dir = str(yaml_path.parent)
    dep.last_yaml = spec["yaml"]
    db.commit()
    return yaml_path, lab_name, spec


def _resolve_subset_short_names(db: Session, topology_id: int) -> Optional[list[str]]:
    """Read the per-topology deployment subset and resolve to clab short names.

    Three return states:
      - None       → no explicit preference saved → caller deploys everything
      - []         → user explicitly cleared the selection → caller refuses
      - [a, b, c]  → explicit subset → caller passes --node-filter
    """
    dep = _get_or_create_deployment(db, topology_id)
    raw = (dep.deployment_subset_json or "").strip()
    if not raw:
        return None  # column never set: implicit "deploy all"
    try:
        node_ids = json.loads(raw)
    except Exception:
        return None
    if not isinstance(node_ids, list):
        return None
    names: list[str] = []
    for nid in node_ids:
        try:
            short = _safe_node_name_for(db, topology_id, int(nid))
        except Exception:
            short = None
        if short:
            names.append(short)
    return names  # may be [] (explicit empty) or populated


def deploy_lab(db: Session, topology_id: int) -> dict:
    """Generate the clab YAML for a topology and run `containerlab deploy`.
    If a deployment subset is configured, only those nodes are brought up
    via `--node-filter` — useful for hosts that can't run the full topology
    at once."""
    _assert_no_other_lab_running(db, topology_id)  # one lab globally at a time
    yaml_path, lab_name, spec = _ensure_fresh_yaml(db, topology_id)

    dep = _get_or_create_deployment(db, topology_id)
    dep.status = "deploying"
    dep.last_error = ""
    dep.started_at = datetime.utcnow()
    dep.finished_at = None
    db.commit()

    subset_names = _resolve_subset_short_names(db, topology_id)
    if subset_names is not None and not subset_names:
        # Explicit empty selection — user cleared the subset. Refuse rather
        # than silently deploying everything.
        dep.status = "failed"
        dep.last_error = "No nodes selected for deployment — pick at least one in the Lab panel."
        db.commit()
        raise LabError(dep.last_error)

    # Run from the YAML's parent dir so clab's working directory
    # (`clab-<lab_name>/`) lands at the same mirrored host+container path as
    # the YAML. Otherwise it ends up at the backend's CWD (/app), which
    # doesn't exist on the host and breaks bind-mounts.
    deploy_cmd = ["containerlab", "deploy", "-t", str(yaml_path), "--reconfigure"]
    if subset_names:
        deploy_cmd += ["--node-filter", ",".join(subset_names)]
    rc, out, err = _run(deploy_cmd, timeout=900, cwd=str(yaml_path.parent))
    if rc != 0:
        dep.status = "failed"
        dep.last_error = (err or out or "containerlab deploy failed")[-4000:]
        dep.finished_at = datetime.utcnow()
        db.commit()
        raise LabError(f"containerlab deploy failed: {dep.last_error}")

    dep.status = "running"
    dep.finished_at = datetime.utcnow()
    db.commit()

    # Lab telemetry add-on sidecars (no-op unless the add-on is enabled).
    # Best-effort: failures here must not fail the deploy.
    try:
        from app.services import lab_addons
        if lab_addons.addon_enabled():
            state = get_deployment_state(db, topology_id)
            nodes = [{"name": c.get("name"), "mgmt_ip": c.get("mgmt_ip"), "kind": c.get("kind")}
                     for c in state.get("containers", [])]
            subs = load_gnmi_subscriptions(db, topology_id)
            lab_addons.start_lab_addons(lab_name, nodes, extra_subscriptions=subs)
    except Exception:
        pass

    return {
        "ok": True, "lab_name": lab_name,
        "skipped_nodes": spec["skipped_nodes"],
        "skipped_links": spec["skipped_links"],
        "output_tail": out[-2000:],
    }


def _container_name_for_node(db: Session, topology_id: int, node_id: int) -> Optional[str]:
    """Resolve a Phanero node id → its clab container name via the lab view."""
    from app.services.lab_view import compute_lab_view
    try:
        view = compute_lab_view(db, topology_id)
    except Exception:
        return None
    for n in view.get("nodes", []):
        if n.get("id") == node_id:
            return n.get("lab_container_name")
    return None


def _safe_node_name_for(db: Session, topology_id: int, node_id: int) -> Optional[str]:
    """Resolve a Phanero node id → its containerlab-side short name (the bit
    after the `clab-<lab_name>-` prefix). Used for `--node-filter`."""
    cname = _container_name_for_node(db, topology_id, node_id)
    if not cname:
        return None
    dep = _get_or_create_deployment(db, topology_id)
    prefix = f"clab-{dep.lab_name}-" if dep.lab_name else ""
    return cname[len(prefix):] if cname.startswith(prefix) else cname


def start_node(db: Session, topology_id: int, node_id: int) -> dict:
    """Start a single node.

    The on-disk YAML is regenerated from the current templates first so the
    partial-deploy path always uses up-to-date image_ref / interfaces /
    links — users don't have to remember to click full Deploy after a
    template edit.

    If the container already exists (was stopped), use containerlab's
    `start --node` subcommand — clab 0.75+ "parks" data-plane veth
    endpoints in a dedicated namespace when a node stops and moves them
    back on start, so connectivity is preserved across the cycle even for
    cisco_xrd (whose userland TAP↔veth bridge would otherwise be lost on
    a plain `docker start`). If the container doesn't exist yet, fall
    through to the partial-deploy path that brings up a brand-new node.
    """
    cname = _container_name_for_node(db, topology_id, node_id)
    if not cname:
        raise LabError("Node has no clab container name (not deployable?)")

    # Regenerate the YAML so per-node Start uses current template state.
    yaml_path, lab_name, _spec = _ensure_fresh_yaml(db, topology_id)
    short = _safe_node_name_for(db, topology_id, node_id) or ""

    # Already exists?
    rc, out, _err = _run(["docker", "inspect", "--format", "{{.State.Status}}", cname], timeout=10)
    if rc == 0 and out.strip():
        state = out.strip()
        if state == "running":
            return {"ok": True, "node_id": node_id, "container": cname, "state": "running", "action": "noop"}
        # clab parks the node's veth endpoints on stop and restores them on
        # start, so we don't have to manage the rewire ourselves anymore.
        rc2, out2, err2 = _run(
            ["containerlab", "start", "-t", str(yaml_path), "--node", short],
            timeout=180, cwd=str(yaml_path.parent),
        )
        if rc2 != 0:
            raise LabError(f"containerlab start --node failed: {err2 or out2}")
        return {"ok": True, "node_id": node_id, "container": cname, "state": "running", "action": "started"}

    # Container doesn't exist — partial-deploy via containerlab using the
    # fresh YAML we just wrote.
    dep = _get_or_create_deployment(db, topology_id)

    # Is anything else already deployed for this lab?
    rc_chk, out_chk, _err_chk = _run(
        ["containerlab", "inspect", "--name", dep.lab_name, "--format", "json"],
        timeout=15,
    )
    other_running = False
    if rc_chk == 0 and out_chk.strip():
        try:
            containers = _normalize_inspect_payload(json.loads(out_chk), dep.lab_name)
            for c in containers:
                if c.get("name") and c.get("name") != cname:
                    other_running = True
                    break
        except Exception:
            pass

    if not other_running:
        # Clean partial-deploy path: clab handles this when nothing else is up.
        # This brings a lab up without going through deploy_lab, so honor the
        # single-active-lab rule here too.
        _assert_no_other_lab_running(db, topology_id)
        short = _safe_node_name_for(db, topology_id, node_id) or ""
        rc, out, err = _run(
            ["containerlab", "deploy", "-t", str(yaml_path), "--node-filter", short],
            timeout=600,
            cwd=str(yaml_path.parent),
        )
        if rc != 0:
            raise LabError(f"containerlab deploy --node-filter failed: {err or out}")
        return {"ok": True, "node_id": node_id, "container": cname, "state": "running", "action": "deployed"}

    # Partial-deploy add-on path: clab's --node-filter refuses on a lab that
    # already has containers, so we mimic what clab would do — docker run the
    # new container and stitch its veth pairs in via `containerlab tools veth
    # create`. v1 limit: only the `linux` kind (FRR etc.) goes through here.
    # Heavier platforms (XRd, cEOS, SRL) need kind-specific setup we don't
    # replicate, so fall back to a clear error for them.
    return _add_node_to_running_lab(db, topology_id, node_id, cname, yaml_path, dep.lab_name)


def _add_node_to_running_lab(db: Session, topology_id: int, node_id: int, cname: str, yaml_path: Path, lab_name: str) -> dict:
    """Spawn a single new container and wire its links into an existing
    clab-managed lab, without going through `clab deploy` (which refuses to
    add nodes to a running lab)."""
    from app.services.lab_view import compute_lab_view
    view = compute_lab_view(db, topology_id)
    node_data = next((n for n in view["nodes"] if n["id"] == node_id), None)
    if not node_data:
        raise LabError(f"Node {node_id} is not in the lab view")

    kind = node_data.get("lab_kind") or ""
    image = node_data.get("lab_image") or ""
    if kind != "linux":
        raise LabError(
            f"Adding a '{kind}' node to a running lab is not yet supported. "
            f"Options: (1) Destroy the whole lab and Deploy again, or "
            f"(2) Stop nodes you don't need with right-click instead of removing them."
        )
    if not image:
        raise LabError(f"Node template has no image_ref set")

    # Short clab-side name = container name minus the "clab-<lab_name>-" prefix
    short = cname[len(f"clab-{lab_name}-"):] if cname.startswith(f"clab-{lab_name}-") else cname

    # Pull the YAML so we know which links involve this node
    try:
        import yaml as _yaml
        with open(yaml_path) as f:
            topo = _yaml.safe_load(f) or {}
    except Exception as e:
        raise LabError(f"Could not read topology YAML: {e}")

    links = ((topo.get("topology") or {}).get("links") or [])
    # Each link is {"endpoints": ["nodeA:iface", "nodeB:iface"]}
    my_links: list[tuple[str, str, str]] = []  # (my_iface, peer_short, peer_iface)
    for l in links:
        eps = l.get("endpoints") or []
        if len(eps) != 2:
            continue
        a_node, _, a_if = eps[0].partition(":")
        b_node, _, b_if = eps[1].partition(":")
        if a_node == short:
            my_links.append((a_if, b_node, b_if))
        elif b_node == short:
            my_links.append((b_if, a_node, a_if))

    # docker run — clab-compatible labels so `containerlab inspect` finds it.
    # We use --network=clab to attach to clab's default management bridge.
    docker_args = [
        "docker", "run", "-d",
        "--name", cname,
        "--hostname", short,
        "--network", "clab",
        "--privileged",
        "--label", f"containerlab={lab_name}",
        "--label", f"clab-node-name={short}",
        "--label", f"clab-node-kind=linux",
        "--label", f"clab-node-type=",
        "--label", f"clab-node-group=",
        "--label", f"clab-topo-file={yaml_path}",
        "--label", f"clab-node-lab-dir={yaml_path.parent}/clab-{lab_name}/{short}",
    ]
    docker_args.append(image)
    rc, out, err = _run(docker_args, timeout=120)
    if rc != 0:
        raise LabError(f"docker run failed: {err or out}")

    # Give the container's netns a moment to settle
    import time
    time.sleep(1)

    # Stitch the veth pairs in
    veth_errors: list[str] = []
    for my_if, peer_short, peer_if in my_links:
        peer_cname = f"clab-{lab_name}-{peer_short}"
        # Verify peer is running before trying to wire
        rc_p, out_p, _ = _run(["docker", "inspect", "--format", "{{.State.Status}}", peer_cname], timeout=5)
        if rc_p != 0 or out_p.strip() != "running":
            veth_errors.append(f"  - {my_if} ↔ {peer_short}:{peer_if}  (peer not running, skipped)")
            continue
        rc_v, out_v, err_v = _run([
            "containerlab", "tools", "veth", "create",
            "-a", f"{cname}:{my_if}",
            "-b", f"{peer_cname}:{peer_if}",
        ], timeout=15, cwd=str(yaml_path.parent))
        if rc_v != 0:
            veth_errors.append(f"  - {my_if} ↔ {peer_short}:{peer_if}  → {err_v or out_v}".strip())

    result_msg = f"Added {cname} to running lab"
    if veth_errors:
        result_msg += f". Some links could not be wired:\n" + "\n".join(veth_errors[:5])
    return {
        "ok": True, "node_id": node_id, "container": cname,
        "state": "running", "action": "added-to-running-lab",
        "links_wired": len(my_links) - len(veth_errors),
        "links_failed": len(veth_errors),
        "message": result_msg,
    }


def stop_node(db: Session, topology_id: int, node_id: int) -> dict:
    """Stop a single node via `containerlab stop --node`.

    clab parks the node's data-plane veth endpoints in a dedicated
    namespace rather than destroying them, so `start_node` can move them
    back without our needing to recreate veths or worry about cisco_xrd's
    TAP↔veth bridge being torn down.
    """
    cname = _container_name_for_node(db, topology_id, node_id)
    if not cname:
        raise LabError("Node has no clab container name")
    yaml_path, _lab_name, _spec = _ensure_fresh_yaml(db, topology_id)
    short = _safe_node_name_for(db, topology_id, node_id) or ""
    rc, out, err = _run(
        ["containerlab", "stop", "-t", str(yaml_path), "--node", short],
        timeout=120, cwd=str(yaml_path.parent),
    )
    if rc != 0:
        raise LabError(f"containerlab stop --node failed: {err or out}")
    return {"ok": True, "node_id": node_id, "container": cname, "action": "stopped"}


def restart_node(db: Session, topology_id: int, node_id: int) -> dict:
    """Restart a single node via `containerlab restart --node`.

    Works uniformly across kinds, including cisco_xrd — clab moves the
    veth endpoints into its parking namespace during the bounce and
    restores them after the container is back up.
    """
    cname = _container_name_for_node(db, topology_id, node_id)
    if not cname:
        raise LabError("Node has no clab container name")
    yaml_path, _lab_name, _spec = _ensure_fresh_yaml(db, topology_id)
    short = _safe_node_name_for(db, topology_id, node_id) or ""
    rc, out, err = _run(
        ["containerlab", "restart", "-t", str(yaml_path), "--node", short],
        timeout=240, cwd=str(yaml_path.parent),
    )
    if rc != 0:
        raise LabError(f"containerlab restart --node failed: {err or out}")
    return {"ok": True, "node_id": node_id, "container": cname, "action": "restarted"}


def destroy_lab(db: Session, topology_id: int) -> dict:
    """Tear down everything Phanero spawned for this topology.

    `containerlab destroy` alone isn't enough in practice:
      - it doesn't always clean up Exited containers (e.g. crashed XRd or
        anything the per-node stop endpoint stopped before destroy ran);
      - it won't catch containers added via the per-node fallback path
        (docker run + veth) since those aren't in the topology YAML.

    So we run clab destroy as best-effort (it handles netns / link cleanup
    nicely when it works) and then sweep `docker rm -f` over every container
    still labeled with this lab name. That label is set on every container
    we spawn — whether via clab or the fallback — so the sweep is exhaustive.
    """
    dep = _get_or_create_deployment(db, topology_id)
    if not dep.lab_name:
        raise LabError("No lab has been deployed for this topology")

    dep.status = "destroying"
    db.commit()

    # Remove lab telemetry add-on sidecars first (best-effort). They
    # carry a phanero-lab-addon label (not containerlab=), so the clab sweep
    # below won't catch them.
    try:
        from app.services import lab_addons
        lab_addons.stop_lab_addons(dep.lab_name)
    except Exception:
        pass

    clab_out = ""
    clab_err = ""
    yaml_path = _yaml_path_for(dep.lab_name)
    if yaml_path.exists():
        _rc, clab_out, clab_err = _run(
            ["containerlab", "destroy", "-t", str(yaml_path), "--cleanup"],
            timeout=300,
            cwd=str(yaml_path.parent),
        )
    else:
        _rc, clab_out, clab_err = _run(
            ["containerlab", "destroy", "--name", dep.lab_name, "--cleanup"],
            timeout=300,
        )

    # Sweep: anything still labeled containerlab=<lab_name> gets force-removed.
    # Covers exited containers, per-node-fallback containers, and anything
    # clab missed. -a so we catch non-running containers too.
    rc, out, _err = _run(
        ["docker", "ps", "-a",
         "--filter", f"label=containerlab={dep.lab_name}",
         "--format", "{{.ID}} {{.Names}}"],
        timeout=30,
    )
    removed: list[str] = []
    failed_removes: list[str] = []
    if rc == 0 and out.strip():
        for line in out.strip().splitlines():
            parts = line.split(" ", 1)
            if not parts:
                continue
            cid = parts[0]
            name = parts[1] if len(parts) > 1 else cid
            r, _o, e = _run(["docker", "rm", "-f", cid], timeout=30)
            if r == 0:
                removed.append(name)
            else:
                failed_removes.append(f"{name}: {(e or _o).strip()[:200]}")

    if failed_removes:
        dep.status = "failed"
        dep.last_error = "Some containers couldn't be removed:\n" + "\n".join(failed_removes)
        dep.finished_at = datetime.utcnow()
        db.commit()
        raise LabError(dep.last_error)

    dep.status = "idle"
    dep.last_error = ""
    dep.finished_at = datetime.utcnow()
    db.commit()
    return {
        "ok": True,
        "removed_containers": removed,
        "removed_count": len(removed),
        "clab_output_tail": (clab_out or clab_err)[-1000:],
    }


# --- Link impairments (netem) — query/modify per-link degradation at runtime ---
# Requires the `sch_netem` kernel module on the host (`sudo modprobe sch_netem`);
# containerlab's container can't load it itself.

def _link_endpoints_for(db: Session, topology_id: int, link_id: int) -> list[tuple[str, str]]:
    """Resolve a (merged) link id to [(container_name, iface), ...] for both
    endpoints, using the same lab view the canvas renders."""
    from app.services.lab_view import compute_lab_view
    view = compute_lab_view(db, topology_id)
    node_container = {n.get("id"): n.get("lab_container_name") for n in view.get("nodes", [])}
    for l in view.get("links", []):
        if l.get("id") == link_id:
            eps = []
            for nid, iface in [(l.get("source_node_id"), l.get("source_interface")),
                               (l.get("target_node_id"), l.get("target_interface"))]:
                cname = node_container.get(nid)
                if cname and iface:
                    eps.append((cname, iface))
            return eps
    return []


def _netem_hint(msg: str) -> str:
    if "netem" in msg.lower() or "modprobe" in msg.lower() or "module" in msg.lower():
        return (msg + "  (If this mentions sch_netem, load it on the host: "
                "`sudo modprobe sch_netem`.)")
    return msg


def get_link_netem(db: Session, topology_id: int, link_id: int) -> dict:
    """Current netem impairments for both endpoints of a link."""
    eps = _link_endpoints_for(db, topology_id, link_id)
    if not eps:
        raise LabError("Link not found in the lab view — deploy the lab first.")
    endpoints = []
    for cname, iface in eps:
        rc, out, err = _run(["containerlab", "tools", "netem", "show", "-n", cname, "--format", "json"], timeout=15)
        impair = None
        if rc == 0 and out.strip():
            try:
                for item in (json.loads(out).get(cname) or []):
                    if item.get("interface") == iface:
                        impair = {
                            "delay": item.get("delay") or "",
                            "jitter": item.get("jitter") or "",
                            "loss": item.get("packet_loss") or 0,
                            "rate": item.get("rate") or 0,
                            "corruption": item.get("corruption") or 0,
                        }
                        break
            except (json.JSONDecodeError, AttributeError):
                pass
        endpoints.append({"container": cname, "interface": iface, "impairment": impair})
    return {"link_id": link_id, "endpoints": endpoints}


def set_link_netem(db: Session, topology_id: int, link_id: int, delay: str = "", jitter: str = "",
                   loss: float = 0, rate: int = 0, corruption: float = 0) -> dict:
    """Apply impairments symmetrically to BOTH endpoints of a link. Empty/zero
    values across the board resets the link instead."""
    eps = _link_endpoints_for(db, topology_id, link_id)
    if not eps:
        raise LabError("Link not found in the lab view — deploy the lab first.")
    if not (delay or jitter or float(loss or 0) or int(rate or 0) or float(corruption or 0)):
        return reset_link_netem(db, topology_id, link_id)
    errors = []
    for cname, iface in eps:
        cmd = ["containerlab", "tools", "netem", "set", "-n", cname, "-i", iface]
        if delay:
            cmd += ["--delay", str(delay)]
        if jitter:
            cmd += ["--jitter", str(jitter)]
        if float(loss or 0):
            cmd += ["--loss", str(loss)]
        if int(rate or 0):
            cmd += ["--rate", str(int(rate))]
        if float(corruption or 0):
            cmd += ["--corruption", str(corruption)]
        rc, out, err = _run(cmd, timeout=20)
        if rc != 0:
            errors.append(f"{cname}:{iface}: {(err or out).strip()[:200]}")
    if errors:
        raise LabError(_netem_hint("netem set failed:\n" + "\n".join(errors)))
    return get_link_netem(db, topology_id, link_id)


def reset_link_netem(db: Session, topology_id: int, link_id: int) -> dict:
    """Clear impairments on both endpoints of a link."""
    eps = _link_endpoints_for(db, topology_id, link_id)
    if not eps:
        raise LabError("Link not found in the lab view — deploy the lab first.")
    for cname, iface in eps:
        _run(["containerlab", "tools", "netem", "reset", "-n", cname, "-i", iface], timeout=15)
    return get_link_netem(db, topology_id, link_id)
