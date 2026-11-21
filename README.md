# Phanero Labs

A network lab platform: draw a topology on the canvas, and every device is a
real router, switch or host running in [containerlab](https://containerlab.dev):
Cisco IOS XR (XRd), IOS XE (CSR 1000v, Catalyst 8000v / 9000v), FRR, Linux
hosts, traffic generators. Edit the topology while the lab runs: cables
patch in within a second and nothing restarts.

- **Labs:** live cabling, per-device start / stop, SSH consoles, packet
  capture on any cable, config snapshots, link impairments, physical NIC /
  VLAN bridging, a management network with DHCP.
- **Traffic generators:** UDP streams with a DSCP per stream, multicast
  sources and receivers (IGMP / MLD, any-source or SSM), a reflector, and live
  rate / loss / jitter / DSCP statistics.
- **AI lab agent** (optional): builds, configures and troubleshoots labs from
  chat.
- **Learning add-on** (optional): graded lessons, modules, and AI-driven
  troubleshooting scenarios (the CCNP SPCOR curriculum ships with it).

## Requirements

- A Linux server (Ubuntu 22.04 / 24.04 tested) with **Docker** installed.
- RAM and CPU for your devices: XRd ~2 GB each, CSR 1000v / Catalyst 8000v
  ~4 GB, Catalyst 9000v ~18 GB, Linux nodes a few MB. A 5-router lab fits in
  16 GB.
- **KVM** (`/dev/kvm`) for VM-based devices (CSR, Catalyst, XRv 9000).
- Free ports **8880** (web UI), **8443** (web UI, https) and **8100**
  (backend, localhost only).

containerlab ≥ 0.83 is required. The container install brings its own; the
native install installs it.

## Quick start (containers)

```bash
git clone https://github.com/vweiss960/network_simulation_app.git phanero-labs
cd phanero-labs
cp .env.example .env          # set ADMIN_PASSWORD and JWT_SECRET_KEY
./scripts/make-cert.sh        # self-signed certificate for https on :8443
./scripts/build-frontend.sh   # builds the web UI (in a Node container)
docker compose up -d --build
```

Open **http://\<server\>:8880** (or **https://\<server\>:8443**; the console's
right-click paste needs https) and log in as `admin` with the password from
`.env`.

On the first start the backend builds the **built-in lab images** in the
background: Linux host, FRR, traffic generator and automation workstation.
This takes several minutes; follow it on the **Images** page or with
`docker compose logs -f backend`. They're rebuilt automatically when an update
changes their source.

After changing the backend or `.env`: `docker compose up -d --build backend`.

## Native install (recommended on a dedicated lab server)

The same thing without the backend container: a systemd service and an nginx
site.

```bash
./scripts/build-frontend.sh          # as your normal user
sudo ./scripts/install-host.sh       # containerlab, Python venv, systemd unit, nginx on :8880
```

The installer creates `.env` from `.env.example` on its first run: set
`ADMIN_PASSWORD` and `JWT_SECRET_KEY`, then
`sudo systemctl restart plabs-backend`. Logs:
`journalctl -u plabs-backend -f`.

Run either the containers or the native install, not both: they use the same
ports.

## Device images

| Devices | Where the image comes from |
|---|---|
| Linux host, FRR, traffic generator, automation workstation | Built automatically (see above). To rebuild by hand: `./scripts/build-images.sh` or the **Images** page |
| Cisco XRd | Download the XRd Control Plane tarball from Cisco, upload it on the **Images** page, click *docker load* |
| CSR 1000v, Catalyst 8000v / 9000v, XRv 9000, vIOS | Upload the qcow2 on the **Images** page (or copy it to the server and register it), then *Build*: it's wrapped into a container with [vrnetlab](https://github.com/srl-labs/vrnetlab) |

Cisco images aren't distributed with Phanero Labs; use your own licensed
copies. New Cisco devices log in with `admin` / `cisco1`.

## Your first lab

1. On the **Labs** page, type a name and click **New lab** (or import a
   containerlab `.clab.yml`).
2. Right-click the canvas → add two devices (e.g. *Cisco XRd Control Plane*)
   and two *Traffic Generators*.
3. Drag between devices to cable them.
4. **Deploy**. Double-click a router for its console, or a traffic generator
   to set its address and start streams.

## Optional features

Set in `.env`, then restart the backend:

| Feature | Settings |
|---|---|
| AI lab agent | `PLABS_AI_ENABLED=1`, `PLABS_AI_API_KEY=<Anthropic API key>`; OpenAI-compatible models with `PLABS_AI_OPENAI_API_KEY` / `PLABS_AI_OPENAI_BASE_URL` / `PLABS_AI_OPENAI_MODELS` |
| Learning (lessons, modules, troubleshooting) | `PLABS_LEARNING_ENABLED=1` |
| Lab telemetry (gNMI → Prometheus) | `PLABS_LAB_TELEMETRY_ENABLED=1` |

Physical networks (bridging lab ports onto host NICs or VLANs, a management
network with DHCP) are set up in the UI: the **Network** page and *Network*
nodes on the canvas. Spare NICs only: the server's own uplink can't be taken
over untagged.

## Troubleshooting

- **A device type says "image not loaded":** build or load its image (see
  *Device images*). Built-in ones may still be building on a first start.
- **Ports in use:** another web server on 8880 / 8443 / 8100. Stop it, or
  change `nginx/plabs.conf` and the backend port.
- **VMs don't start:** check `/dev/kvm` exists and the server has the RAM.
- **XRd devices fail to boot:** the native installer sets the inotify sysctls
  XRd needs (`/etc/sysctl.d/99-plabs.conf`); with the containers, set
  `fs.inotify.max_user_instances = 64000` and
  `fs.inotify.max_user_watches = 1048576` on the host.

## Development

See [CLAUDE.md](CLAUDE.md) for the architecture and how to test changes
(backend tests run in a container: no host Python needed).
