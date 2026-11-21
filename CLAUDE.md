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
  https on **:8443** (self-signed, `./scripts/make-cert.sh` → `nginx/certs/`,
  run once before the first start): browsers only allow clipboard reads —
  the console's right-click paste — on https.
  After backend changes: `docker compose restart backend`.

Lab images: `./scripts/build-images.sh` builds `plabs/host`, `plabs/frr`,
`plabs/traffic` (traffic generator, FROM plabs/host) and `plabs/workstation` (automation toolbox: ncclient, netmiko, scrapli, pyATS,
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
  throwaway XRd/FRR/c8000v lab, configures devices live, hot-cables /
  unplugs / replugs (incl. a Lab Switch), adds/removes devices, and checks
  configs + pings + that nothing restarted (`--no-vm` skips the 8000v;
  ~9 min with it). Run after touching reconcile / YAML / host_net.

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
  Device port halves are enslaved into them by `apply_patches`.
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
- Credentials: new Cisco devices are provisioned with `admin` / `cisco1`
  (`DeviceKind.default_*`; `create_node` stores them as
  `credentials["provision"]`). XRd gets them in its base config (so every
  new XRd has a startup config); vrnetlab VMs via clab `env`
  `USERNAME`/`PASSWORD`. Devices without `provision` predate this and keep the
  image's own login (`builtin_*`: XRd clab/clab@123, XE admin/admin) with
  their YAML unchanged. Editing the login on the canvas only changes what the
  app logs in with, never the device's definition.
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
- **Hot linking: pre-provisioned ports + host patch panel** (verified by
  `scripts/live_edit_test.py`). Every port of every device is a veth whose
  other half is on the host (`host:pp<t>n<node>p<idx>`, `host_net.port_name`),
  created with the device. Cables are *not* in the clab YAML:
  `host_net.apply_patches` joins two halves with `tc mirred` (all frames,
  LACP/LLDP/STP included) or enslaves a half into a Network node's Linux
  bridge (Network nodes are host bridges, not clab nodes); unplugged halves
  are down. It's incremental (correct ports untouched) and runs after every
  deploy / reconcile / node start and at startup. A reconcile whose YAML is
  unchanged (cabling only) skips containerlab entirely → cabling takes
  effect in a second and never restarts anything. Why: XRd and vrnetlab VMs
  only see interfaces present at boot and never pick up a replaced veth.
  - Only adding/removing a device, changing its interface count / image /
    startup config restarts that device. On a deployed lab those three can
    be applied now or saved for the next restart (`NodeUpdate.defer` →
    `NodeInstance.pending`; `topology_ops.apply_pending` runs on the device's
    Restart / Start and on lab Deploy; `DELETE …/nodes/{id}/pending`
    discards).
  - Devices can be started one at a time (`NodeInstance.enabled`): Start on
    an undeployed lab deploys only that device (others off); Start on an off
    device in a running lab adds just it (ports pre-provisioned → its cables
    patch in, nothing else restarts). Off devices are skipped by
    `compute_lab_view` (`off: true`). `POST …/lab/deploy?all=true` (Deploy /
    "Start all") powers everything on; without it, only devices that are on. containerlab's *in-place* recreate
    gives a node only the changed links (XRd `XR_INTERFACES`, vrnetlab
    `CLAB_INTFS`), so `_recreate_plan` / `_apply_plan` remove such nodes
    first and containerlab creates them fresh.
  - Link state: Linux/FRR see an unplugged port as down (NO-CARRIER); XRd CP
    and IOS-XE (virtio, even with QEMU `set_link off`) keep showing up/up —
    traffic stops, protocols notice via hellos/BFD. Same as CML.
  - XRd keeps its config in the lab dir (`xr-storage`) across recreation.
    VMs don't: `_carry_running_configs` saves their running config into
    `NodeInstance.carry_config`, which boots instead of `startup_config` until
    the lab is destroyed or the startup config is edited.
    `clab_yaml.day0_config` makes IOS/XE running configs bootable (drops the
    self-signed cert — it crashed CVAC — license/platform/version lines, adds
    explicit `no shutdown`, keeps `end`).
- Lab power: **Stop** (`POST …/lab/stop`, `lab_service.stop_lab`) has every
  running device save its running config as its own startup config
  (`device_exec.save_config`: write memory / FRR write; XR commits persist),
  refuses if one can't unless `force`, then `containerlab stop` — containers
  are kept, status `stopped`, host networking (bridges, mgmt network, leases)
  kept. Deploy on a stopped lab = `start_lab`: `containerlab start` on the
  last applied YAML, patches, then a reconcile for edits made while stopped
  (request_sync is a no-op while stopped). **Wipe** (UI name for destroy)
  needs the lab name typed and snapshots running devices first by default.
- Console for a booting vrnetlab VM streams `docker logs` (vrnetlab owns the
  serial port while booting) and switches to SSH when it answers.

### Packet capture (`services/packet_capture.py`, `routes/capture.py`)
- Cable right-click → Capture packets (or the link panel, with a BPF capture
  filter) opens a capture tab in the console drawer (`components/CaptureView`).
  tshark runs on the host half of the cable's device port (the source end;
  the device end when the other is a Network node): both directions, every
  frame type, no device involvement.
- One tshark per capture writes the pcapng (`-w`) and prints field rows
  (`-P -T fields`) that the WebSocket `…/lab/captures/{id}/stream` relays.
  Packet details (`-V -x`) and display filters (`-Y` → frame numbers) run
  tshark over the file. Limits: 100 MB per capture, 8 running.
- Captures are in memory; files live in `$PLABS_CAPTURE_DIR` or the temp dir
  (not `lab_state`: tshark can't read back its own file on that bind mount),
  are swept at startup, and stop on lab destroy. Closing the tab deletes one.

### Traffic generator nodes (`services/traffic_gen.py`, `routes/traffic.py`, `device_images/traffic`)
- Template "Traffic Generator" = kind `linux`, image `plabs/traffic` (`device_kinds.is_traffic_image`),
  icon `traffic`. Its main process `plabs-traffic` (one Python file) sends UDP streams (per-stream
  DSCP, rate, size, TTL; unicast or multicast, v4/v6), joins groups (ASM, or SSM with a source,
  IGMPv3 / MLDv2) or listens on unicast ports, and reflects stream packets back to their sender
  (keep or re-mark DSCP). Packets carry magic + seq + timestamp + stream name → per-flow pps,
  loss, jitter and **arrival DSCP** (IP_RECVTOS / IPV6_RECVTCLASS); reflections give RTT and the
  DSCP at the reflector / on return.
- Config = `NodeInstance.traffic` (normalized by `traffic_gen.normalize`), written to
  `<lab dir>/traffic/<node>/config.json`; clab binds that dir at `/etc/plabs-traffic` (constant
  bind → editing never changes the YAML / restarts). The daemon re-reads it every second and
  writes `stats.json` there. `GET|PUT /api/topologies/{t}/nodes/{n}/traffic` (live, no reconcile);
  topology_io exports `traffic:`. UI: double-click → `TrafficView` tab in the console drawer.
- Lesson check `type: traffic` (`lesson_checks.eval_traffic`, reads stats.json via docker exec):
  `flow: {stream|src|dst|group|port}` or `stream: NAME` (+ `reflected`), `min_pps`, `max_pps`,
  `max_loss`, `dscp` (name / list), `dscp_at_reflector`, `present: false`. The multicast and QoS
  lessons use generators for every source / receiver.

### Learning add-on (`PLABS_LEARNING_ENABLED`, default off)
Lessons and modules (curricula later) are an optional add-on. Off: the
`/api/lessons…` / `/api/modules…` routes answer 404 (`main._learning_on`),
bundled packs aren't seeded, topologies report no `lesson_id` / `lesson_role`
/ `lesson_ai_policy` and the agent ignores lesson policies — lesson labs act as
ordinary labs; nothing is deleted. `/api/features` → `learning_enabled`; the
nav shows one **Learning** menu (Curricula — soon —, Modules, Lessons, Troubleshooting).

### Lessons (`models/lesson.py`, `services/lessons.py`, `services/lesson_checks.py`, `routes/lessons.py`)
- A lesson = starting topology (topology_io YAML: devices, cables, starting
  configs, annotations, `enabled: false` devices) + tasks graded by checks +
  markdown content, with level, track (enterprise / service_provider / …),
  topic, tags, `ai_policy` (teach | hints | off) and `hints_mode` (open |
  on_request | none). Lessons are grouped into modules (below); curricula don't exist yet.
- Packs: a zip of `lesson.yaml`, `topology.yaml`, `tasks.yaml`, `content.md`
  (`## ` sections = readings). Import validates everything (check devices
  exist, regexes compile…); a duplicate slug → 409 unless `mode=replace|copy`.
  Bundled packs in `curriculum/lessons/*/` (`PLABS_CURRICULUM_DIR`) are
  imported at startup — newer `version`s too, unless edited in the app.
- Checks (`lesson_checks`): `cli` (command + expect regex/contains…), `route`
  (IPv4 or IPv6 prefix + protocol code, optional `metric` / `via`, present /
  absent; XR / XE / FRR table formats incl. XE's maskless "is subnetted"
  lines and IPv6 `OI`/`OE2`/`ON2` codes),
  `ping` (`device_exec.ping_command` / `ping_success`), `config`. A run opens
  ONE session per device with all its commands (XRd resets parallel SSH logins).
- Start = `LessonAttempt` + its own Topology (`lesson_role="attempt"`, built
  with `topology_io.build_into`); resume reuses it; reset wipes the lab and
  rebuilds it. Authoring: `lesson_role="authoring"` lab edited on the normal
  canvas, saved back (optionally capturing running configs, minus mgmt / clab
  user / SSH bits — `starting_config_from_running`).
- Plain Linux hosts (not FRR) run their startup config lines as clab `exec`
  commands (`clab_yaml.linux_exec_commands`). Background traffic: `setsid … >/dev/null 2>&1
  < /dev/null &` (e.g. iperf2 multicast senders / receivers — `plabs/host` has iperf2 + iperf3);
  IPv6 addresses need `nodad` or a bind to them fails at boot. Linux hosts stay in IGMPv2
  compatibility mode ~260 s after their querier moves to v3.
- FRR nodes (`plabs/frr`): containerlab ignores `startup-config` on Linux nodes, so
  `clab_yaml.frr_startup` mounts the startup config as `/etc/frr/frr.conf` (bind) and runs
  its `$ `-prefixed lines as `exec` shell commands (MACsec, `wpa_supplicant` MKA, sysctls).
  `plabs/frr` includes wpa_supplicant 2.10 built with `macsec_linux`, patched so
  `MKA_EAPOL_BCAST=1` sends MKPDUs to broadcast (Cisco `eapol destination-address
  broadcast-address`): VXLAN switches drop EAPoL to 01:80:c2:00:00:03.
- AI policy: the lab agent appends a lesson block (tasks + progress + rules)
  to its system prompt; `hints` drops the change tools; `off` → 403 + no Ask
  AI bar. Frontend: `pages/LessonsPage`, `LessonPage`, `LessonEditor`,
  `workspace/LessonPanel` (+ `AuthoringBar`), `lesson/TopologyPreview`, `lesson/meta`.
- Offline check of a pack (no app, no JWT — builds the topology straight in containerlab,
  applies starting configs, then each task's solution and polls its checks with the app's
  evaluator): `docker exec plabs-backend-1 sh -c 'cd <repo>/backend && PYTHONPATH=. python -u
  ../scripts/lesson_offline_test.py ../curriculum/lessons/<slug> [--keep] [--reuse --from TASK]'`.
  It runs inside the backend container: don't restart the backend while it runs.
- Live check of a lesson's checks: `python3 scripts/lesson_solve_test.py <jwt> <slug>`
  (starts the lesson, applies each task's reference solution, expects every check to pass;
  `--authoring` uses the lesson's authoring lab instead of the token user's attempt; `--keep`).
- The BGP lessons share one topology: `bgp-path-selection-policy` and `bgp-addpaths-pic`
  start where `bgp-sp-core` ends — their `topology.yaml` is generated by
  `scripts/build_bgp_lessons.py` (bgp-sp-core's topology + its solutions; `do …` exec
  lines dropped). Solutions may contain `do clear …` lines (XE soft-clear / session
  reset for new capabilities), which `configure` sends in config mode.
- Attempts record `lesson_version`; when the bundled lesson gets a newer version the
  attempt is `outdated` and the UI offers Restart (its lab was built from the old topology).
- Platform limits for lesson design (verified live, `sp-high-availability`): XRd Control
  Plane runs **no BFD** (no line card: sessions stay DOWN, `BFD_MP_DOWNLOAD_NO_LC`, and IS-IS
  with `bfd fast-detect` never forms the adjacency) — BFD lessons use XE–XE links; CSR 1000v
  BFD works (echo mode) but not on a Port-channel. LACP bundles work XR–XR and XR–XE (fast
  rate, `bundle minimum-active links` / XE `lacp min-bundle`). GR/NSF work (XR `process
  restart bgp|isis`, XE helper keeps routes stale); XRd has one RP, so NSR is config-only
  (BGP NSR is on by default there). XR applies a GR change only on the next session reset.
- Services (verified live): XRd **label-switches** (LDP, L3VPN, EoMPLS PWs and BGP-LU transit
  through XR P routers all work; XR needs `mpls oam` to answer MPLS ping/traceroute), forwards
  multicast as core and last-hop router (`multicast-pim-igmp-mld`: IPv4 PIM-SM, SSM, BIDIR with
  DF election, BSR, IGMPv3; IPv6 PIMv6 SM + SSM, MLDv2 — all with real traffic), enforces interface ACLs, and is a BGP flowspec controller (CSR installs
  the rules as client). XRd has **no QoS data plane** (MQC config incl. HQoS / EXP classes / WRED is accepted and output policies attach — `show policy-map targets` — but report *Service Policy not installed*; input policies fail at commit, NetIO *Invalid argument*; `qos-classify-police-shape` grades XR on config), and **no** uRPF, LPTS policer config, CFM, G.8032
  or MACsec, and is **no L2VPN PE**: xconnects, bridge domains and VPLS PWs come up
  (LDP-signalled, state up) but the bridge domain / xconnect forwards nothing (AC and PW
  counters stay 0; a local host MAC may show, learned on the RP, but never crosses a PW).
  So an XRd PE is graded on signalling only (`l2vpn-carrier-ethernet` R2: VPLS member +
  XR-to-XE E-Line), traffic between CSR PEs; it label-switches other PEs' PWs fine. CSR 1000v does LDP sync / session protection / auth / label filtering, CoPP,
  uRPF, MQC (police, shape, WRED), PIM SM/SSM/BIDIR, IGMPv3/MLD, EVC rewrite, 802.1ad,
  EoMPLS xconnect, VPLS VFI (bridge-domain), 802.1ad on EFPs, CFM **down MEPs only** (no `cfm mep` under
  a service instance → no up MEPs on xconnects), 802.3ah, flowspec client — but is capped at **1 Mb/s**
  unlicensed (C8000V: 20 Mb/s). No image here does MACsec, G.8032, 802.1ah PBB, E-Tree or
  BGPsec: readings only.

### Modules (`models/module.py`, `services/modules.py`, `routes/modules.py`)
- A module = ordered items `{lesson: slug, section, title, objectives}` (a
  blueprint section per lesson) + `overview.md` + level / track / `blueprint`.
  Lessons are referenced by **slug**; a lesson can be in several modules and
  still runs on its own (lesson detail lists `modules` → "Part of").
- Progress is derived per user from lesson attempts (a lesson completed once
  stays completed after a restart); `next_lesson_id` drives "Continue".
- Packs: `module.yaml` (+ `lessons:` items), `overview.md`, and on export
  `lessons/<slug>/…` lesson packs, so a module imports anywhere. Import
  installs missing lessons and newer versions of lessons not edited in the
  app, keeps the rest. Bundled: `curriculum/modules/*/` (seeded after lessons).
- Frontend: `pages/ModulesPage`, `ModulePage`, `ModuleEditor` (nav "Modules").

### Troubleshooting / Chaos (`models/scenario.py`, `services/troubleshooting.py`, `services/chaos_ai.py`, `routes/troubleshooting.py`)
- A scenario = a known-good **baseline** (`topology.yaml`, generated by
  `scripts/build_troubleshooting_baselines.py` from a lesson + all its verified
  solutions; no annotations / notes / link labels), `health.yaml` (checks that
  all pass when healthy; `scope: service` = customer-visible), `docs.md`
  (Guided only), `scenario.yaml` (+ `managed` routers — diffed for the debrief —
  and `parties` who can raise tickets) and `faults.yaml`: a **template catalog**.
  `targets` = parts of the network (`{kind: [instance vars…]}`; link instances
  have `a_*`/`b_*`, `devices: [..]` hits several routers); `templates` =
  generic breaks (protocol × category adjacency | advertisement) with `target`,
  `sides: [a, b]` (each end → `dev_*` / `peer_*`), `device` (e.g. `"{rr}"`,
  the target's own dev stays `{target_dev}`), `requires`, per-OS
  `config.{iosxr|iosxe}.{inject,revert}`, symptoms, hints, scope, ticket.
  `expand()` = every template × instance × side whose device's OS has a
  config → **candidates** (id `template@key`, cached per scenario version);
  add a router / link / session by adding a target instance. Placeholders
  `{POLICY1} {ROUTEMAP1} {PREFIXLIST1} {KEYCHAIN1} {SECRET1}` get neutral names
  per round; validation rejects named objects that aren't placeholders or
  baseline names, and descriptions in injects.
- A round (`start_round` → background `_job`): wait for devices → revert the
  previous round → health must pass → **the Chaos model plans it**
  (`chaos_ai.plan_round`: picks N candidates from the menu + the learner's
  history, theme + back-story) → `choose_faults` keeps valid picks (distinct
  templates, disjoint scopes) and fills up randomly (spread over protocol ×
  category) → inject → wait for symptoms → `_confirm`: settle
  (`SETTLE_SECONDS`) and re-check `CONFIRM_PASSES` times — a fault the network
  heals by itself (e.g. BGP re-forming from the other side) is reverted,
  `Scenario.mark_ineffective` (never drawn again until the pack is saved /
  re-imported; `live_candidates`) and replaced, and the story re-told →
  snapshot managed configs → `write_tickets` from the checks *still* failing
  after the settle (never the cause; leak guard, reporter must be a party;
  detail per difficulty: guided = experience + "NOC notes", standard =
  experience only, expert = one sentence). Check = every fault's symptoms + every
  health check pass → solved, however it was fixed: **Rebuild during a round**
  keeps the round (`rebuilt`) and counts. Give up → revealed; both show the
  debrief (what changed, the fix, how to spot it, story, the learner's config
  diff) and `write_debrief` judges the fix from that diff.
- Labs: `Topology.lesson_role = "troubleshoot"` (+ `scenario_id`); admins
  verify every candidate on a `troubleshoot_test` lab: `python3 scripts/chaos_catalog_test.py
  <jwt> <slug> [--only template|candidate,…] [--skip …] [--keep]` (inject → symptom shows → revert → healthy).
- Difficulty: guided (docs, full health, hints, AI coach = lab agent `hints`
  policy with a troubleshooting prompt), standard (service checks only,
  counted hints, no AI), expert (tickets only).
- The Chaos model (`PLABS_CHAOS_MODEL`, default `claude-sonnet-5-5`); with AI
  off every step falls back (random draw, catalog ticket text, no narrative).

### Lab agent (`services/lab_agent.py`, `services/llm.py`, `routes/agent.py`)
- Chat on the canvas (Ask AI bar → `components/agent/AIChatPanel`), streamed as
  SSE from `POST /api/topologies/{id}/agent/chat`; conversations live in memory
  (`lab_agent._SESSIONS`, per lab + model). `GET /api/ai/status` lists the
  models whose provider has a key.
- Default model Claude Sonnet 5.5 (`claude-sonnet-5-5`; Opus 5.5 selectable), effort `high`, adaptive
  thinking, prompt caching, server-side refusal `fallbacks: "default"`);
  OpenAI models via `PLABS_AI_OPENAI_*` (any OpenAI-compatible endpoint).
- Tools are the UI's own code paths: lab state (kinds, OS, cabling, state),
  running config / show / ping (`device_exec`), apply config, add / cable /
  remove devices, group boxes / notes, start + wait. Claude also gets
  `web_search` / `web_fetch` restricted to Cisco doc sites; OpenAI models get
  `fetch_cisco_doc`. Every change is logged in `agent_actions`
  (`GET …/agent/actions`). The prompt is Cisco-centric; vendor specifics stay
  in `device_exec` / `device_kinds`.

### Layout
`backend/app/`: `config.py` (env + paths, defaults inside the repo),
`routes/` (topologies, nodes, links, node_templates, lab, lab_console, auth),
`services/` (lab_service, clab_yaml, lab_view, device_kinds, topology_ops,
topology_io, qcow_images, lab_addons, llm), `models/`, `schemas/`.
Schema is `create_all()` only — no migrations yet; add additive migrations if
a released DB needs a new column.

`frontend/src/`: `stores/useLabStore.ts` (all workspace state + actions),
`components/workspace/` (Workspace — no palette: devices are added from the canvas
right-click menu via `canvas/AddDevicePicker`; Inspector shows only for a
selected device/cable; LabSummary opens as the right-hand "Lab info" panel from the
header's info button; NetworkInspector),
`components/canvas/` (Canvas, DeviceNode, CableEdge, ContextMenu, icons = classic
network symbols, AnnotationNodes = group boxes / text / notes stored in
`Topology.annotations_json` via `GET|PUT /api/topologies/{id}/annotations`,
CanvasToolbar, AskAIBar = entry point for the future lab agent; the canvas
is light, the panels dark),
`components/pages/` (LabsHome, TemplatesPage, ImagesPage, NetworkPage), reused
`LabConsoleDrawer`, `LinkNetemModal`, `LoginPage`, `panels/QcowImageManager`,
`panels/UserManagementPanel`. `utils/api.ts` wraps every endpoint.

### Env
`PLABS_*` (see `.env.example`). AI: `PLABS_AI_ENABLED`, `PLABS_AI_API_KEY`
(Anthropic), `PLABS_AI_MODEL`, optional `PLABS_AI_OPENAI_API_KEY` /
`PLABS_AI_OPENAI_BASE_URL` (any OpenAI-compatible endpoint, e.g. LiteLLM) /
`PLABS_AI_OPENAI_MODELS`; the provider follows the chosen model.

### Host notes
The server also runs the original Phanero (ports 80/3000/8000) and other apps
on 8080/3100/3939 — pick other ports. eno1 is the main LAN; eno2-4 and
enp66s0f0-3 are spare NICs for lab networks. Live-test bridges only on spare
NICs; never put eno1 in access mode.
