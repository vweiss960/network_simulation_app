# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this is

**Phanero Labs** — a network *simulation* lab platform, forked (clean copy) from
`../network_modeling_app` (Phanero, a network planning tool). The canvas **is**
the lab: every node is a real containerlab device (container or vrnetlab VM)
with its own kind + image, and edits to a deployed topology apply live.
Roadmap (see the plan in `~/.claude/plans/so-i-want-your-wobbly-lampson.md`):
bridged-NIC management network + Network nodes mapping lab ports onto host
NICs / trunk VLANs (Phase 2, done), device
access layer + SSH console + snapshots + automation workstation (3, done), CCNP SP / Automation curriculum with
graded labs (4), an AI lab agent (5).

Backend: FastAPI + SQLAlchemy + SQLite. Frontend: React + Vite + TypeScript,
Zustand, React Flow, xterm.js.

## Running

Two equivalent ways; don't run both (they share ports 8100 / 8880).

- **Native (recommended on the lab server):** `./scripts/build-frontend.sh`
  then `sudo ./scripts/install-host.sh` → systemd `plabs-backend` (uvicorn on
  127.0.0.1:8100, venv `/opt/plabs/venv`) + nginx site on **:8880**.
  After backend changes: `sudo systemctl restart plabs-backend`;
  logs: `journalctl -u plabs-backend -f`.
- **Containers:** `./scripts/build-frontend.sh && docker compose up -d --build`
  (backend privileged + host network; nginx `web` on :8880).
  After backend changes: `docker compose restart backend`.

Lab images: `./scripts/build-images.sh` builds `plabs/host`, `plabs/frr` and
`plabs/workstation` (automation toolbox: ncclient, netmiko, scrapli, pyATS,
Ansible + Cisco collections, gnmic, pyang).
Vendor images (XRd tarballs, qcow2) are loaded/built from the Images page;
vrnetlab is cloned into `$DEVICE_IMAGES_DIR/vrnetlab` at startup.

**containerlab ≥ 0.83 is required** — live editing relies on plain
`containerlab deploy` reconciling a running lab in place (0.75 refuses).

## Verifying changes

- Backend tests (no host Python deps needed):
  `cd backend && docker build -q -t plabs-backend:dev . && docker run --rm -v $PWD:/app/backend plabs-backend:dev python -m pytest`
  (`conftest.py` binds a temp SQLite DB + LAB_STATE_DIR and stubs
  `lab_service.request_sync` in the `client` fixture).
- Frontend: `./scripts/build-frontend.sh` runs `tsc --noEmit` + `vite build`
  in a Node container (the host has no Node).
- Live: deploy a small lab of `plabs/host` / `plabs/frr` nodes via the UI or
  API and check `docker ps --filter label=containerlab=<lab_name>`.
- Live editing end to end: `python3 scripts/live_edit_test.py <jwt>` builds a
  throwaway XRd/FRR/c8000v lab, configures devices live, adds/removes nodes
  and links, and checks configs + pings after each step (`--no-vm` skips the
  8000v; ~20 min with it). Run after touching reconcile / YAML / host_net.

## Architecture

### The canvas is the lab
- `NodeInstance` carries `kind`, `image_ref`, `interfaces` (list of
  `{name, kernel}`: device-native config name + clab endpoint name),
  `mgmt_ipv4`, credentials, `startup_config`. Links reference interfaces by
  **config name**; `lab_view.compute_lab_view` maps them to kernel names.
- `services/device_kinds.py` is the single registry of every kind (interface
  naming, default creds, exec CLI, boot time, sizing, VM or container). Add a
  kind there and nowhere else.
- `services/topology_ops.py` holds node/link creation rules (auto-naming,
  next-free interface, validation) shared by routes, import and (later)
  lab sessions + the agent.
- Any route that changes what's deployed calls `lab_service.request_sync()`:
  if the topology has a deployed lab, a background, per-topology-serialized,
  coalesced reconcile regenerates the YAML and runs `containerlab deploy`.
  Position/colour/notes edits don't reconcile.
- `services/clab_yaml.py` emits the topology file: no host `ports:`. For
  `cisco_xrd`, a startup config is prefixed with containerlab's own first-boot
  defaults (it *replaces* them otherwise).
- Multiple labs can run at once; `_assert_capacity` checks RAM instead.

### Host networking (`services/host_net.py`, `lab_network.py`)
- **Management network is per lab, chosen on the canvas.** A Network node
  (access or trunk) with `network.mgmt = true` makes that NIC/VLAN the lab's
  mgmt network (one per lab; changing it needs the lab destroyed). Addressing
  comes from that network's own DHCP server (`services/dhcp.py`: raw
  AF_PACKET client on the host bridge, per-device client-id + locally
  administered MAC): the server leases an address for itself on the bridge
  (to reach devices; it also yields subnet/gateway/DNS) and one per device,
  held in `mgmt_leases`, renewed past T1 by a background thread while the lab
  is deployed, requested again on redeploy (sticky), released on teardown /
  device delete. Docker network `plabs-m-<bridge>` is created with
  `inhibit_ipv4` (never put the upstream gateway IP on the host bridge); every
  device gets an explicit `mgmt-ipv4`. Labs without a mgmt Network node use
  the internal `plabs-mgmt` network (subnet on the Network page, server-only
  reach). DHCP / bridge setup only happens on deploy/reconcile
  (`generate_clab_yaml(assign_ips=True)`), never on previews.
- Claiming a NIC untagged releases it from the host (NetworkManager unmanaged
  + conf.d drop-in, addresses flushed); only the primary default-route NIC is
  refused.
- **Network nodes** (`kind: bridge` in the registry; `NodeInstance.network`
  = `{mode: internal|access|trunk, nic, vlan}`) become clab `kind: bridge`
  nodes. Bridges are named deterministically (`pl-<nic>[-<vlan>]`, internal
  `pl-sw<topo>-<node>`), so labs using the same NIC/VLAN share one segment.
  Ports grow as devices are cabled; host-side veth names are host-unique
  (`host_net.bridge_port_name`).
- `lab_service._prepare_host_network` creates bridges / VLAN sub-interfaces /
  the mgmt network before every deploy and reconcile; `gc_host_network`
  removes unused `pl-*` bridges after reconcile/destroy; `restore_host_network`
  recreates them at startup (reboot recovery). A `DOCKER-USER` ACCEPT rule per
  bridge stops br_netfilter + Docker's FORWARD DROP eating bridged frames.
- Access (untagged) mode is refused on the server's own uplink (default-route /
  addressed NIC); trunk VLANs on it are fine.
- Schema: `init_db()` → `add_missing_columns()` adds new model columns to an
  existing SQLite DB automatically (additive only).

### Device access (`services/device_exec.py`, `routes/device.py`)
- `target_for(db, topology, node)` resolves mgmt IP (lease or container IP),
  credentials (`lab_view.node_credentials`) and family; every caller (REST,
  snapshots, future grader / agent) goes through it.
- Network OSes: interactive SSH (`CliSession`, asyncssh, prompt regex
  `_PROMPT`, yes/no questions `_QUESTION`). `configure()` = IOS-XR candidate +
  commit (abort on any `%` error), XE/EOS line by line; `replace=True` →
  XR `commit replace` (auto-confirmed). Linux kinds: `docker exec`
  (`vtysh` for FRR images). NETCONF (ncclient, RPC errors → readable
  ExecError → HTTP 502), RESTCONF (requests), gNMI (pygnmi, insecure, kind's
  gNMI port; set takes `[[path, value]]`).
- Console WebSocket: SSH to the mgmt IP for network OSes (full CLI incl.
  config mode); falls back to `docker exec` (XRd wrapper / vrnetlab serial)
  when SSH isn't up; `?cli=` forces docker exec. Workstation images open
  `su - student`.
- Snapshots (`config_snapshots`): per device or whole lab (shared label).
  Restore `live` (XR commit replace / FRR) or `reboot` (becomes startup config
  → device recreated).
- containerlab quirks: its reconcile ignores `startup-config` changes, so
  startup files are content-hashed and `lab_service._startup_changed` removes
  those containers before reconciling — *and* their node directory
  (`clab-<lab>/<node>/`): containerlab copies the startup config there once
  (vrnetlab `/config`, XRd `first-boot.cfg` + `xr-storage`) and reuses the
  stale copy otherwise.
- **Live edits must not break neighbours** (verified by an end-to-end harness:
  add/remove nodes + links on a running XRd/FRR/c8000v lab, check configs +
  pings). Facts that drive the design:
  - Kinds without `live_links` (everything but `linux`) are recreated on any
    link change. containerlab's *in-place* recreate is buggy: the node gets
    only the changed links (XRd `XR_INTERFACES`, vrnetlab `CLAB_INTFS`), so
    `_recreate_plan` predicts those nodes and `_apply_plan` removes them
    first → containerlab creates them as new nodes with every interface.
  - A running XRd / vrnetlab VM binds its interfaces at boot: a hot-added
    or *replaced* veth (same name) is never picked up. So every cable that
    touches such a kind is split into two host-side halves
    (`host:pw<t>l<link>a|b`) joined by `tc mirred` (`host_net.join_wires`,
    re-run after every deploy/reconcile/node start). Recreating a device then
    replaces only its own half; neighbours are untouched.
  - XRd keeps its config in the lab dir (`xr-storage`) across recreation.
    VMs don't: `_carry_running_configs` saves their running config into
    `NodeInstance.carry_config`, which boots instead of `startup_config` until
    the lab is destroyed or the startup config is edited.
    `clab_yaml.day0_config` makes IOS/XE running configs bootable (drops the
    self-signed cert — it crashed CVAC — license/platform/version lines, adds
    explicit `no shutdown`, keeps `end`).
- Console for a booting vrnetlab VM streams `docker logs` (vrnetlab owns the
  serial port while booting) and switches to SSH when it answers.

### Layout
`backend/app/`: `config.py` (env + paths, defaults inside the repo),
`routes/` (topologies, nodes, links, node_templates, lab, lab_console, auth),
`services/` (lab_service, clab_yaml, lab_view, device_kinds, topology_ops,
topology_io, qcow_images, lab_addons, llm), `models/`, `schemas/`.
Schema is `create_all()` only — no migrations yet; add additive migrations if
a released DB needs a new column.

`frontend/src/`: `stores/useLabStore.ts` (all workspace state + actions),
`components/workspace/` (Workspace, Palette, Inspector, NetworkInspector, LabSummary),
`components/canvas/` (Canvas, DeviceNode, CableEdge, ContextMenu),
`components/pages/` (LabsHome, TemplatesPage, ImagesPage, NetworkPage), reused
`LabConsoleDrawer`, `LinkNetemModal`, `LoginPage`, `panels/QcowImageManager`,
`panels/UserManagementPanel`. `utils/api.ts` wraps every endpoint.

### Env
`PLABS_*` (see `.env.example`). AI: `services/llm.py` provider abstraction
(`PLABS_AI_PROVIDER` anthropic | openai | litellm).

### Host notes
The server also runs the original Phanero (ports 80/3000/8000) and other apps
on 8080/3100/3939 — pick other ports. eno1 is the main LAN; eno2-4 and
enp66s0f0-3 are spare NICs for lab networks. Live-test bridges only on spare
NICs; never put eno1 in access mode.
