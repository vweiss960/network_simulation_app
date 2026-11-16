#!/usr/bin/env bash
# Native install of Phanero Labs on the lab host (Ubuntu/Debian).
#
#   sudo ./scripts/install-host.sh
#
# Installs: containerlab, a Python venv for the backend, a systemd unit
# (plabs-backend), an nginx site on :8880, and persistent sysctls for
# XRd-class devices. Re-runnable. Run ./scripts/build-frontend.sh first (as
# your normal user) so frontend/dist exists.
set -euo pipefail

CLAB_VERSION="${CLAB_VERSION:-0.83.0}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
VENV=/opt/plabs/venv
UNIT=/etc/systemd/system/plabs-backend.service

[ "$(id -u)" = 0 ] || { echo "Run as root (sudo)." >&2; exit 1; }
command -v docker >/dev/null || { echo "Docker is required (https://docs.docker.com/engine/install/)." >&2; exit 1; }

if docker ps --format '{{.Names}}' | grep -qx 'plabs-backend-1'; then
  echo "The containerized stack is running (ports 8100/8880). Stop it first: docker compose down" >&2
  exit 1
fi

echo "==> packages"
apt-get update -qq
# Don't let the nginx package auto-start its stock :80 site during install —
# port 80 may already belong to another app on this host.
had_policy=0; [ -e /usr/sbin/policy-rc.d ] && had_policy=1
if [ $had_policy = 0 ]; then printf '#!/bin/sh\nexit 101\n' > /usr/sbin/policy-rc.d; chmod +x /usr/sbin/policy-rc.d; fi
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
  python3-venv python3-dev build-essential nginx iproute2 iptables git make jq telnet openssh-client curl ca-certificates tshark
[ $had_policy = 0 ] && rm -f /usr/sbin/policy-rc.d
# Drop the stock default site (it listens on :80) if it is the untouched symlink.
if [ -L /etc/nginx/sites-enabled/default ] && [ "$(readlink -f /etc/nginx/sites-enabled/default)" = /etc/nginx/sites-available/default ]; then
  rm -f /etc/nginx/sites-enabled/default
fi

echo "==> containerlab ${CLAB_VERSION}"
have="$(containerlab version 2>/dev/null | awk '/version:/{print $2; exit}' || true)"
if [ "$have" != "$CLAB_VERSION" ]; then
  curl -fsSL "https://github.com/srl-labs/containerlab/releases/download/v${CLAB_VERSION}/containerlab_${CLAB_VERSION}_linux_amd64.tar.gz" \
    | tar -xz -C /usr/local/bin containerlab
  chmod +x /usr/local/bin/containerlab
fi
containerlab version | grep -E "version:" || true

echo "==> backend venv (${VENV})"
mkdir -p "$(dirname "$VENV")"
[ -x "$VENV/bin/python" ] || python3 -m venv "$VENV"
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q -r "$REPO/backend/requirements.txt"

echo "==> sysctls (/etc/sysctl.d/99-plabs.conf)"
cat > /etc/sysctl.d/99-plabs.conf <<SYS
# Phanero Labs: XRd needs many inotify instances/watches per device.
fs.inotify.max_user_instances = 64000
fs.inotify.max_user_watches = 1048576
SYS
sysctl -q -p /etc/sysctl.d/99-plabs.conf
modprobe sch_netem 2>/dev/null && echo sch_netem > /etc/modules-load.d/plabs.conf || true

echo "==> systemd unit (${UNIT})"
[ -f "$REPO/.env" ] || { cp "$REPO/.env.example" "$REPO/.env"; echo "   created $REPO/.env — set ADMIN_PASSWORD / JWT_SECRET_KEY"; }
cat > "$UNIT" <<UNITEOF
[Unit]
Description=Phanero Labs backend
After=network-online.target docker.service
Wants=network-online.target
Requires=docker.service

[Service]
Type=simple
User=root
WorkingDirectory=${REPO}/backend
EnvironmentFile=-${REPO}/.env
Environment=DATABASE_URL=sqlite:///${REPO}/backend/data/plabs.db
Environment=PYTHONUNBUFFERED=1
ExecStart=${VENV}/bin/uvicorn app.main:app --host 127.0.0.1 --port 8100
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
UNITEOF
systemctl daemon-reload
systemctl enable --now plabs-backend
systemctl restart plabs-backend

echo "==> nginx site (:8880, https :8443)"
mkdir -p /usr/share/plabs
ln -sfn "$REPO/frontend/dist" /usr/share/plabs/www
sudo -u "${SUDO_USER:-root}" "$REPO/scripts/make-cert.sh" || "$REPO/scripts/make-cert.sh"
install -d -m 755 /etc/nginx/plabs-certs
install -m 644 "$REPO/nginx/certs/plabs.crt" /etc/nginx/plabs-certs/plabs.crt
install -m 600 "$REPO/nginx/certs/plabs.key" /etc/nginx/plabs-certs/plabs.key
install -m 644 "$REPO/nginx/plabs.conf" /etc/nginx/conf.d/plabs.conf
nginx -t && systemctl enable --now nginx && systemctl reload nginx

echo
echo "Phanero Labs is running: http://$(hostname -f 2>/dev/null || hostname):8880"
echo "  logs:    journalctl -u plabs-backend -f"
echo "  restart: sudo systemctl restart plabs-backend   (after backend code changes)"
[ -d "$REPO/frontend/dist" ] || echo "  NOTE: frontend/dist missing — run ./scripts/build-frontend.sh"
