# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this is

**Phanero Labs** — a network *simulation* lab platform, forked (clean copy) from
`../network_modeling_app` (Phanero, a network planning tool). The canvas **is**
the lab: every node is a real containerlab device (container or vrnetlab VM)
with its own kind + image, and edits to a deployed topology apply live.
Roadmap (see the plan in `~/.claude/plans/so-i-want-your-wobbly-lampson.md`):
bridged-NIC management network with a unique IP per device (Phase 2), device
access layer + more Cisco kinds (3), CCNP SP / Automation curriculum with
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

Lab images: `./scripts/build-images.sh` builds `plabs/host` and `plabs/frr`.
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
- `services/clab_yaml.py` emits the topology file: no host `ports:`; every lab
  shares the `plabs-mgmt` Docker network (default 172.30.0.0/22,
  `PLABS_LAB_MGMT_SUBNET`). For `cisco_xrd`, a startup config is prefixed with
  containerlab's own first-boot defaults (it *replaces* them otherwise).
- Multiple labs can run at once; `_assert_capacity` checks RAM instead.

### Layout
`backend/app/`: `config.py` (env + paths, defaults inside the repo),
`routes/` (topologies, nodes, links, node_templates, lab, lab_console, auth),
`services/` (lab_service, clab_yaml, lab_view, device_kinds, topology_ops,
topology_io, qcow_images, lab_addons, llm), `models/`, `schemas/`.
Schema is `create_all()` only — no migrations yet; add additive migrations if
a released DB needs a new column.

`frontend/src/`: `stores/useLabStore.ts` (all workspace state + actions),
`components/workspace/` (Workspace, Palette, Inspector, LabSummary),
`components/canvas/` (Canvas, DeviceNode, CableEdge, ContextMenu),
`components/pages/` (LabsHome, TemplatesPage, ImagesPage), reused
`LabConsoleDrawer`, `LinkNetemModal`, `LoginPage`, `panels/QcowImageManager`,
`panels/UserManagementPanel`. `utils/api.ts` wraps every endpoint.

### Env
`PLABS_*` (see `.env.example`). AI: `services/llm.py` provider abstraction
(`PLABS_AI_PROVIDER` anthropic | openai | litellm).

### Host notes
The server also runs the original Phanero (ports 80/3000/8000) and other apps
on 8080/3100/3939 — pick other ports. eno1 is the main LAN; eno2-4 and
enp66s0f0-3 are spare NICs intended for the lab networks (Phase 2).
