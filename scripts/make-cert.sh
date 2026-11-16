#!/usr/bin/env bash
# Self-signed TLS certificate for the https listener (:8443). Browsers only
# let a page read the clipboard over https (console right-click paste), so
# use https://<server>:8443 and accept the certificate once.
# Covers the host name(s), localhost and every global IPv4 of the host's
# NICs (not Docker / lab bridges). Re-run with --force after an IP change.
set -euo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)/nginx/certs"
mkdir -p "$DIR"
if [ -s "$DIR/plabs.crt" ] && [ "${1:-}" != "--force" ]; then
  echo "Certificate exists: $DIR/plabs.crt (--force to regenerate)"; exit 0
fi
SAN="DNS:localhost,DNS:$(hostname),IP:127.0.0.1"
FQDN="$(hostname -f 2>/dev/null || true)"
[ -n "$FQDN" ] && [ "$FQDN" != "$(hostname)" ] && SAN="$SAN,DNS:$FQDN"
for ip in $(ip -4 -o addr show scope global | awk '$2 !~ /^(docker|br-|pl-|veth|clab)/ {split($4,a,"/"); print a[1]}'); do
  SAN="$SAN,IP:$ip"
done
openssl req -x509 -newkey rsa:2048 -nodes -days 3650 -sha256 \
  -keyout "$DIR/plabs.key" -out "$DIR/plabs.crt" \
  -subj "/CN=$(hostname)/O=Phanero Labs" -addext "subjectAltName=$SAN" 2>/dev/null
chmod 644 "$DIR/plabs.crt"; chmod 640 "$DIR/plabs.key"
echo "Wrote $DIR/plabs.crt ($SAN)"
