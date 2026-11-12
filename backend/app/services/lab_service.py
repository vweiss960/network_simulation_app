"""Containerlab lab orchestration service.

Wraps the `containerlab` CLI to deploy/destroy/inspect labs derived from Phanero Labs
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


from app.config import LAB_STATE_DIR
from app.config import DEVICE_IMAGES_DIR


class LabError(Exception):
    """Lab orchestration failure with a user-facing message."""


CLAB_BIN = os.environ.get("PLABS_CONTAINERLAB_BIN", "containerlab")


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
    if cmd and cmd[0] == "containerlab":
        cmd = [CLAB_BIN, *cmd[1:]]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)
        return proc.returncode, proc.stdout, proc.stderr
    except FileNotFoundError:
        return 127, "", f"{cmd[0]}: not found (is it installed on the lab host?)"
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
    hugepage_nodes = 0
    total_nodes = 0
    if topology_id is not None:
        try:
            from app.services.lab_view import compute_lab_view
            view = compute_lab_view(db, topology_id)
            total_nodes = len(view.get("nodes", []))
            for n in view.get("nodes", []):
                if n.get("lab_kind") in ("cisco_xrd", "cisco_xrv9k"):
                    xrd_count += 1
                if n.get("requires_hugepages"):
                    hugepage_nodes += 1
        except Exception:
            xrd_count = 0

    # Sizes — XRd Control Plane's documented recommendations:
    #   ~4000 inotify instances/watches per node. Hugepages are NOT required
    #   for the Control Plane variant (the vRouter needed them, but vRouter
    #   isn't supported by containerlab).
    needed_inotify_instances = max(64000, 4000 * xrd_count) if xrd_count else 0
    needed_inotify_watches = max(1048576, 4000 * xrd_count) if xrd_count else 0
    # XRd vRouter wants ~3 GiB of 2 MiB hugepages per instance.
    needed_hugepages = 1536 * hugepage_nodes

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
    hugepages_target = max(needed_hugepages, 1536)
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
        "reason": "XRd vRouter needs ~3 GiB of 2 MiB hugepages per device; other kinds don't use them",
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
                "sudo tee /etc/sysctl.d/99-plabs-lab.conf >/dev/null <<'EOF'\n"
                f"{body}\n"
                "EOF\n"
                "sudo sysctl -p /etc/sysctl.d/99-plabs-lab.conf\n"
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
        "sudo rm -f /etc/sysctl.d/99-plabs-lab.conf\n"
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
    fd, tmp_name = tempfile.mkstemp(suffix=suffix, prefix="plabs-clab-")
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


# ---------------------------------------------------------------------------
# Lifecycle
#
# The canvas IS the lab. Every change to a deployed topology regenerates the
# containerlab YAML and runs `containerlab deploy` again; containerlab (>=
# 0.83) reconciles a deployed lab in place — it adds new nodes and links,
# removes deleted ones, recreates only nodes whose definition changed, and
# leaves everything else running. Reconciles are serialized per topology and
# coalesced: edits made while one is running trigger exactly one follow-up.
# ---------------------------------------------------------------------------

import threading

_ACTIVE_STATES = ("deploying", "running", "syncing")
_sync_locks: dict[int, threading.Lock] = {}
_sync_pending: set[int] = set()
_sync_guard = threading.Lock()


def _lock_for(topology_id: int) -> threading.Lock:
    with _sync_guard:
        return _sync_locks.setdefault(topology_id, threading.Lock())


def _boot_threshold(kind: str) -> int:
    from app.services.device_kinds import get_kind
    return get_kind(kind).boot_seconds


def _normalize_inspect_payload(data, lab_name: str) -> list[dict]:
    """Flatten `containerlab inspect --format json` across clab versions:
    {"containers": [...]}, [...], or {"<lab_name>": [...]} (0.75+)."""
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    if isinstance(data.get("containers"), list):
        return data["containers"]
    if lab_name and isinstance(data.get(lab_name), list):
        return data[lab_name]
    out: list[dict] = []
    for v in data.values():
        if isinstance(v, list):
            out.extend(v)
    return out


def _strip_cidr(ip: str) -> str:
    ip = (ip or "").strip()
    if ip in ("", "N/A"):
        return ""
    return ip.split("/", 1)[0]


def _container_started(cid: str) -> tuple[Optional[str], Optional[int]]:
    rc, out, _ = _run(["docker", "inspect", "--format", "{{.State.StartedAt}}", cid], timeout=10)
    if rc != 0 or not out.strip() or out.startswith("0001-"):
        return None, None
    s = out.strip()
    try:
        from datetime import timezone
        iso = s.replace("Z", "+00:00")
        if "." in iso:  # docker emits nanoseconds; fromisoformat takes ≤ 6 digits
            head, rest = iso.split(".", 1)
            tz_at = max(rest.find("+"), rest.find("-"))
            frac, tz = (rest[:tz_at], rest[tz_at:]) if tz_at >= 0 else (rest, "")
            iso = f"{head}.{frac[:6]}{tz}"
        dt = datetime.fromisoformat(iso)
        return s, int((datetime.now(timezone.utc) - dt).total_seconds())
    except Exception:
        return s, None


def _enrich_container(c: dict) -> dict:
    cid = c.get("container_id") or c.get("ContainerID") or c.get("name") or ""
    kind = c.get("kind") or "linux"
    state = (c.get("state") or "").lower()
    started_at, uptime = _container_started(cid) if cid else (None, None)
    threshold = _boot_threshold(kind)
    if state == "running":
        ready = "up" if (uptime is not None and uptime >= threshold) else "booting"
    elif state in ("exited", "dead"):
        ready = "down"
    elif state in ("created", "restarting", "paused"):
        ready = "starting"
    else:
        ready = "unknown"
    return {
        **c,
        "started_at": started_at, "uptime_sec": uptime,
        "mgmt_ip": _strip_cidr(c.get("ipv4_address") or ""),
        "ready_state": ready, "boot_threshold_sec": threshold,
    }


def inspect_lab(lab_name: str) -> list[dict]:
    if not lab_name:
        return []
    rc, out, _err = _run(["containerlab", "inspect", "--name", lab_name, "--format", "json"], timeout=20)
    if rc != 0 or not out.strip():
        return []
    try:
        return [_enrich_container(c) for c in _normalize_inspect_payload(json.loads(out), lab_name)]
    except Exception:
        return []


def get_deployment_state(db: Session, topology_id: int) -> dict:
    """Recorded lab state plus live per-container state, keyed back to node ids."""
    from app.services.lab_view import compute_lab_view
    dep = _get_or_create_deployment(db, topology_id)
    view = compute_lab_view(db, topology_id)
    lab_name = dep.lab_name or view["lab_name"]
    containers = inspect_lab(lab_name)
    by_cname = {c.get("name"): c for c in containers}
    nodes = {}
    from app.services import host_net
    for n in view["nodes"]:
        if n.get("is_bridge"):
            exists = host_net.link_exists(n["bridge"])
            seg = n["segment"]
            uplink = seg.get("nic") if seg.get("mode") != "internal" else None
            link_ok = (not uplink) or host_net.carrier(uplink)
            nodes[n["id"]] = {
                "container": None, "bridge": n["bridge"],
                "state": "running" if exists else "absent",
                "ready_state": ("up" if link_ok else "down") if exists else "absent",
                "uptime_sec": None, "mgmt_ip": "", "carrier": link_ok if uplink else None,
            }
            continue
        c = by_cname.get(n["lab_container_name"])
        nodes[n["id"]] = {
            "container": n["lab_container_name"],
            "state": (c or {}).get("state", "absent"),
            "ready_state": (c or {}).get("ready_state", "absent"),
            "uptime_sec": (c or {}).get("uptime_sec"),
            "mgmt_ip": (c or {}).get("mgmt_ip") or n["mgmt_ip"],
        }
    return {
        "status": dep.status,
        "lab_name": lab_name,
        "lab_dir": dep.lab_dir,
        "last_error": dep.last_error,
        "started_at": dep.started_at.isoformat() if dep.started_at else None,
        "finished_at": dep.finished_at.isoformat() if dep.finished_at else None,
        "pending_sync": topology_id in _sync_pending,
        "nodes": nodes,
        "skipped_nodes": view["skipped_nodes"],
        "containers": containers,
    }


def _ensure_fresh_yaml(db: Session, topology_id: int) -> tuple[Path, str, dict]:
    """Regenerate + write the topology YAML; record it on the deployment row."""
    spec = generate_clab_yaml(db, topology_id)
    lab_name = spec["lab_name"]
    yaml_path = _yaml_path_for(lab_name)
    yaml_path.write_text(spec["yaml"])
    dep = _get_or_create_deployment(db, topology_id)
    dep.lab_name = lab_name
    dep.lab_dir = str(yaml_path.parent)
    dep.last_yaml = spec["yaml"]
    db.commit()
    return yaml_path, lab_name, spec


def _active_specs(db: Session, exclude: Optional[int] = None) -> list[dict]:
    out = []
    for d in db.query(LabDeployment).filter(LabDeployment.status.in_(_ACTIVE_STATES + ("failed",))).all():
        if d.topology_id == exclude or not d.lab_name:
            continue
        try:
            out.append(generate_clab_yaml(db, d.topology_id, assign_ips=False))
        except Exception:
            pass
    return out


def _prepare_host_network(db: Session, topology_id: int, spec: dict) -> None:
    """Create everything the lab's YAML expects to already exist on the host:
    the bridged mgmt network and one bridge per Network node."""
    from app.services import host_net, lab_network
    from app.services.host_net import HostNetError, Segment
    settings = lab_network.get_settings(db)
    segs = [Segment.from_dict(b["segment"]) for b in spec["resource_estimate"]["bridges"]]
    # Untagged (access) and tagged (trunk) use of the same NIC coexist: the
    # kernel hands tagged frames to the VLAN sub-interface before the bridge.
    try:
        nics = host_net.list_nics()
        for b, seg in zip(spec["resource_estimate"]["bridges"], segs):
            if settings.bridged and b["bridge"] == settings.bridge:
                raise HostNetError("A Network node can't use the management network's NIC/VLAN")
            # A NIC we already enslaved shows up as "protected" only if it has
            # an IP — validate against the live NIC list each time anyway.
            host_net.validate_segment(seg, nics)
        lab_network.ensure_mgmt(settings)
        for b, seg in zip(spec["resource_estimate"]["bridges"], segs):
            host_net.ensure_bridge(b["bridge"], seg, settings.mtu or None)
    except HostNetError as e:
        raise LabError(str(e))


def gc_host_network(db: Session) -> list[str]:
    """Remove pl-* bridges no deployed lab (or the mgmt network) uses."""
    from app.services import host_net, lab_network
    keep = {b["bridge"] for sp in _active_specs(db) for b in sp["resource_estimate"]["bridges"]}
    settings = lab_network.get_settings(db)
    if settings.bridged and settings.nic:
        keep.add(settings.bridge)
    return host_net.gc_bridges(keep)


def restore_host_network(db: Session) -> None:
    """After a host reboot bridges are gone; recreate them for running labs."""
    from app.services import lab_network
    try:
        lab_network.ensure_mgmt(lab_network.get_settings(db))
    except Exception as e:
        print(f"[startup] mgmt network: {e}", flush=True)
    for sp in _active_specs(db):
        dep = db.query(LabDeployment).filter(LabDeployment.lab_name == sp["lab_name"]).first()
        try:
            if dep:
                _prepare_host_network(db, dep.topology_id, sp)
        except Exception as e:
            print(f"[startup] host network for {sp['lab_name']}: {e}", flush=True)


def _assert_capacity(db: Session, topology_id: int, spec: dict) -> None:
    """Refuse a deploy whose RAM estimate (plus every other running lab's)
    exceeds what the host has. Multiple labs can run side by side — devices
    are addressed by their own mgmt IP, so labs don't contend for host ports."""
    from app.services.clab_yaml import get_host_capacity
    cap = get_host_capacity()
    if not cap.get("memory_mb"):
        return
    others = (
        db.query(LabDeployment)
        .filter(LabDeployment.topology_id != topology_id, LabDeployment.status.in_(_ACTIVE_STATES))
        .all()
    )
    used = 0
    for o in others:
        try:
            used += generate_clab_yaml(db, o.topology_id)["resource_estimate"]["memory_mb"]
        except Exception:
            pass
    need = spec["resource_estimate"]["memory_mb"]
    budget = int(cap["memory_mb"] * 0.9)
    if used + need > budget:
        raise LabError(
            f"Not enough memory: this lab needs ~{need} MB, other running labs use ~{used} MB, "
            f"host budget is {budget} MB (90% of {cap['memory_mb']} MB). Stop another lab first."
        )


def _clab_deploy(yaml_path: Path) -> tuple[int, str, str]:
    # No --reconfigure: plain deploy reconciles an existing lab in place.
    return _run(["containerlab", "deploy", "-t", str(yaml_path)], timeout=1800, cwd=str(yaml_path.parent))


def _start_addons(db: Session, topology_id: int, lab_name: str) -> None:
    try:
        from app.services import lab_addons
        if lab_addons.addon_enabled():
            state = get_deployment_state(db, topology_id)
            nodes = [{"name": c.get("name"), "mgmt_ip": c.get("mgmt_ip"), "kind": c.get("kind")}
                     for c in state.get("containers", [])]
            lab_addons.start_lab_addons(lab_name, nodes, extra_subscriptions=load_gnmi_subscriptions(db, topology_id))
    except Exception:
        pass


def deploy_lab(db: Session, topology_id: int) -> dict:
    """Deploy (or reconcile) the whole topology."""
    with _lock_for(topology_id):
        _sync_pending.discard(topology_id)
        yaml_path, lab_name, spec = _ensure_fresh_yaml(db, topology_id)
        if not spec["included_nodes"]:
            raise LabError("Nothing to deploy — add devices with an image to the canvas first.")
        _assert_capacity(db, topology_id, spec)
        _prepare_host_network(db, topology_id, spec)
        dep = _get_or_create_deployment(db, topology_id)
        dep.status, dep.last_error = "deploying", ""
        dep.started_at, dep.finished_at = datetime.utcnow(), None
        db.commit()
        rc, out, err = _clab_deploy(yaml_path)
        dep.finished_at = datetime.utcnow()
        if rc != 0:
            dep.status = "failed"
            dep.last_error = (err or out or "containerlab deploy failed")[-4000:]
            db.commit()
            raise LabError(f"containerlab deploy failed: {dep.last_error}")
        dep.status = "running"
        db.commit()
    _start_addons(db, topology_id, lab_name)
    return {
        "ok": True, "lab_name": lab_name,
        "skipped_nodes": spec["skipped_nodes"], "skipped_links": spec["skipped_links"],
        "output_tail": (out or "")[-2000:],
    }


def _sync_worker(topology_id: int) -> None:
    from app.models.database import SessionLocal
    lock = _lock_for(topology_id)
    while True:
        with lock:
            if topology_id not in _sync_pending:
                return
            _sync_pending.discard(topology_id)
            db = SessionLocal()
            try:
                dep = _get_or_create_deployment(db, topology_id)
                if dep.status not in ("running", "syncing", "failed") or not dep.lab_name:
                    return
                dep.status = "syncing"
                db.commit()
                try:
                    yaml_path, _lab, spec = _ensure_fresh_yaml(db, topology_id)
                    if not spec["included_nodes"]:
                        # Last device removed → tear the lab down entirely.
                        db.commit()
                        _destroy_locked(db, topology_id)
                        return
                    _prepare_host_network(db, topology_id, spec)
                    rc, out, err = _clab_deploy(yaml_path)
                except LabError as e:
                    rc, out, err = 1, "", str(e)
                dep = _get_or_create_deployment(db, topology_id)
                dep.finished_at = datetime.utcnow()
                if rc != 0:
                    dep.status = "failed"
                    dep.last_error = (err or out or "reconcile failed")[-4000:]
                else:
                    dep.status, dep.last_error = "running", ""
                db.commit()
                if rc == 0:
                    try:
                        gc_host_network(db)
                    except Exception:
                        pass
            finally:
                db.close()


def request_sync(db: Session, topology_id: int) -> bool:
    """Called after any canvas change. If the topology has a deployed lab,
    schedule a background reconcile (coalesced). Returns True if scheduled."""
    dep = db.query(LabDeployment).filter(LabDeployment.topology_id == topology_id).first()
    if not dep or dep.status not in ("running", "syncing", "failed") or not dep.lab_name:
        return False
    if dep.status == "failed" and not inspect_lab(dep.lab_name):
        return False  # failed before anything came up; wait for an explicit Deploy
    _schedule_sync(topology_id)
    return True


def _schedule_sync(topology_id: int) -> None:
    with _sync_guard:
        already = topology_id in _sync_pending
        _sync_pending.add(topology_id)
    if not already:
        threading.Thread(target=_sync_worker, args=(topology_id,), daemon=True).start()


def _node_short_name(db: Session, topology_id: int, node_id: int) -> str:
    from app.services.lab_view import compute_lab_view
    for n in compute_lab_view(db, topology_id)["nodes"]:
        if n["id"] == node_id:
            return n["clab_name"]
    raise LabError("Node is not deployable (no image set?)")


def _node_action(db: Session, topology_id: int, node_id: int, action: str, timeout: int) -> dict:
    dep = _get_or_create_deployment(db, topology_id)
    if dep.status not in _ACTIVE_STATES + ("failed",) or not dep.lab_name:
        raise LabError("The lab is not deployed. Deploy it first.")
    short = _node_short_name(db, topology_id, node_id)
    with _lock_for(topology_id):
        yaml_path = _yaml_path_for(dep.lab_name)
        if not yaml_path.exists():
            yaml_path, _lab, _spec = _ensure_fresh_yaml(db, topology_id)
        rc, out, err = _run(["containerlab", action, "-t", str(yaml_path), "--node", short],
                            timeout=timeout, cwd=str(yaml_path.parent))
    if rc != 0:
        raise LabError(f"containerlab {action} --node {short} failed: {err or out}")
    return {"ok": True, "node_id": node_id, "action": action}


def start_node(db: Session, topology_id: int, node_id: int) -> dict:
    """Start a stopped node. A node that was never created (added while the
    lab was down, or a failed reconcile) is brought up by a reconcile."""
    from app.services.lab_view import compute_lab_view
    cname = next((n["lab_container_name"] for n in compute_lab_view(db, topology_id)["nodes"]
                  if n["id"] == node_id), None)
    if not cname:
        raise LabError("Node is not deployable (no image set?)")
    rc, out, _ = _run(["docker", "inspect", "--format", "{{.State.Status}}", cname], timeout=10)
    if rc != 0:
        dep = _get_or_create_deployment(db, topology_id)
        if dep.status in _ACTIVE_STATES or dep.status == "failed":
            _schedule_sync(topology_id)
            return {"ok": True, "node_id": node_id, "action": "reconcile"}
        return deploy_lab(db, topology_id)
    if out.strip() == "running":
        return {"ok": True, "node_id": node_id, "action": "noop"}
    return _node_action(db, topology_id, node_id, "start", 300)


def stop_node(db: Session, topology_id: int, node_id: int) -> dict:
    return _node_action(db, topology_id, node_id, "stop", 180)


def restart_node(db: Session, topology_id: int, node_id: int) -> dict:
    return _node_action(db, topology_id, node_id, "restart", 600)


def _destroy_locked(db: Session, topology_id: int) -> dict:
    dep = _get_or_create_deployment(db, topology_id)
    if not dep.lab_name:
        dep.status = "idle"
        db.commit()
        return {"ok": True, "removed_containers": [], "removed_count": 0}
    dep.status = "destroying"
    db.commit()
    try:
        from app.services import lab_addons
        lab_addons.stop_lab_addons(dep.lab_name)
    except Exception:
        pass
    yaml_path = _yaml_path_for(dep.lab_name)
    if yaml_path.exists():
        _rc, clab_out, clab_err = _run(["containerlab", "destroy", "-t", str(yaml_path), "--cleanup"],
                                       timeout=600, cwd=str(yaml_path.parent))
    else:
        _rc, clab_out, clab_err = _run(["containerlab", "destroy", "--name", dep.lab_name, "--cleanup"], timeout=600)
    # Sweep anything clab missed (exited containers etc.) by its lab label.
    rc, out, _ = _run(["docker", "ps", "-a", "--filter", f"label=containerlab={dep.lab_name}",
                       "--format", "{{.ID}} {{.Names}}"], timeout=30)
    removed, failed = [], []
    if rc == 0:
        for line in out.strip().splitlines():
            cid, _, name = line.partition(" ")
            r, o, e = _run(["docker", "rm", "-f", cid], timeout=60)
            if r == 0:
                removed.append(name or cid)
            else:
                failed.append(f"{name or cid}: {(e or o).strip()[:200]}")
    dep.finished_at = datetime.utcnow()
    if failed:
        dep.status = "failed"
        dep.last_error = "Some containers couldn't be removed:\n" + "\n".join(failed)
        db.commit()
        raise LabError(dep.last_error)
    dep.status, dep.last_error = "idle", ""
    db.commit()
    try:
        gc_host_network(db)
    except Exception:
        pass
    return {"ok": True, "removed_containers": removed, "removed_count": len(removed),
            "clab_output_tail": (clab_out or clab_err)[-1000:]}


def destroy_lab(db: Session, topology_id: int) -> dict:
    """Tear down every container this topology's lab spawned."""
    _sync_pending.discard(topology_id)
    with _lock_for(topology_id):
        return _destroy_locked(db, topology_id)


def destroy_lab_by_name(lab_name: str) -> None:
    """Best-effort teardown for a topology that is being deleted."""
    yaml_path = Path(LAB_STATE_DIR) / lab_name / "topology.clab.yml"
    if yaml_path.exists():
        _run(["containerlab", "destroy", "-t", str(yaml_path), "--cleanup"], timeout=600, cwd=str(yaml_path.parent))
    rc, out, _ = _run(["docker", "ps", "-aq", "--filter", f"label=containerlab={lab_name}"], timeout=30)
    for cid in out.split():
        _run(["docker", "rm", "-f", cid], timeout=60)


# --- Link impairments (netem) ----------------------------------------------
# Requires the `sch_netem` kernel module on the host (`sudo modprobe sch_netem`).

def _link_endpoints_for(db: Session, topology_id: int, link_id: int) -> list[tuple[str, str]]:
    from app.services.lab_view import compute_lab_view
    view = compute_lab_view(db, topology_id)
    cname = {n["id"]: n["lab_container_name"] for n in view["nodes"]}
    for l in view["links"]:
        if l["id"] == link_id:
            return [(cname[nid], iface) for nid, iface in
                    [(l["source_node_id"], l["source_interface"]), (l["target_node_id"], l["target_interface"])]
                    if cname.get(nid) and iface]
    return []


def _netem_hint(msg: str) -> str:
    if any(w in msg.lower() for w in ("netem", "modprobe", "module")):
        return msg + "  (If this mentions sch_netem, load it on the host: `sudo modprobe sch_netem`.)"
    return msg


def get_link_netem(db: Session, topology_id: int, link_id: int) -> dict:
    eps = _link_endpoints_for(db, topology_id, link_id)
    if not eps:
        raise LabError("Link not found in the lab — deploy the lab first.")
    endpoints = []
    for cname, iface in eps:
        rc, out, _ = _run(["containerlab", "tools", "netem", "show", "-n", cname, "--format", "json"], timeout=15)
        impair = None
        if rc == 0 and out.strip():
            try:
                for item in (json.loads(out).get(cname) or []):
                    if item.get("interface") == iface:
                        impair = {
                            "delay": item.get("delay") or "", "jitter": item.get("jitter") or "",
                            "loss": item.get("packet_loss") or 0, "rate": item.get("rate") or 0,
                            "corruption": item.get("corruption") or 0,
                        }
                        break
            except (json.JSONDecodeError, AttributeError):
                pass
        endpoints.append({"container": cname, "interface": iface, "impairment": impair})
    return {"link_id": link_id, "endpoints": endpoints}


def set_link_netem(db: Session, topology_id: int, link_id: int, delay: str = "", jitter: str = "",
                   loss: float = 0, rate: int = 0, corruption: float = 0) -> dict:
    eps = _link_endpoints_for(db, topology_id, link_id)
    if not eps:
        raise LabError("Link not found in the lab — deploy the lab first.")
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
    eps = _link_endpoints_for(db, topology_id, link_id)
    if not eps:
        raise LabError("Link not found in the lab — deploy the lab first.")
    for cname, iface in eps:
        _run(["containerlab", "tools", "netem", "reset", "-n", cname, "-i", iface], timeout=15)
    return get_link_netem(db, topology_id, link_id)


def local_docker_images() -> set[str]:
    rc, out, _ = _run(["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"], timeout=20)
    if rc != 0:
        return set()
    return {l.strip() for l in out.splitlines() if l.strip() and "<none>" not in l}
