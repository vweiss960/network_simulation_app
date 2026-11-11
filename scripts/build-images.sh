#!/usr/bin/env bash
# Build the open-source lab images that ship with Phanero Labs.
# Vendor images (XRd tarballs, qcow2 VMs) are loaded/built from the Images page.
set -euo pipefail
cd "$(dirname "$0")/.."
build() { echo "==> $2"; docker build -t "$2" "device_images/$1"; }
build linux-host            plabs/host:latest
build frr-lab               plabs/frr:latest
if [ -d device_images/automation-workstation ]; then
  build automation-workstation plabs/workstation:latest
fi
