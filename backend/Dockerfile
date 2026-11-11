FROM python:3.11-slim

WORKDIR /app

# Runtime deps:
#   - curl / ca-certificates: pull binaries below
#   - iproute2 / iptables: netns + veth tooling that containerlab uses
#   - jq: parse `containerlab inspect --format json` output from shell helpers
#   - git / make: clone hellt/vrnetlab and run its per-platform Makefiles
#     for the QCOW VM image build flow (Lab Mode)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        curl ca-certificates iproute2 iptables jq git make && \
    rm -rf /var/lib/apt/lists/*

# Docker CLI (static binary). The backend talks to the host's Docker daemon via
# the mounted /var/run/docker.sock — we do NOT run a daemon inside this container.
ARG DOCKER_VERSION=25.0.5
RUN curl -fsSL "https://download.docker.com/linux/static/stable/x86_64/docker-${DOCKER_VERSION}.tgz" \
    | tar -xz -C /tmp && \
    cp /tmp/docker/docker /usr/local/bin/docker && \
    rm -rf /tmp/docker

# Containerlab — the engine that deploys / wires up vendor network containers
# according to a YAML topology file. Phanero generates that YAML and shells
# out to this binary.
# 0.75.0 introduced `containerlab start/stop/restart <node>` with parked
# dataplane interfaces, removing the need to redeploy the whole topology
# when bouncing a single node. Tested upstream on nokia_srl/srsim/sros and
# juniper_vmx — Cisco XRd not in the test matrix, so verify before relying
# on it (fallback: revert to 0.55.0 and keep the redeploy path).
ARG CLAB_VERSION=0.75.0
RUN curl -fsSL "https://github.com/srl-labs/containerlab/releases/download/v${CLAB_VERSION}/containerlab_${CLAB_VERSION}_linux_amd64.tar.gz" \
    | tar -xz -C /usr/local/bin/ containerlab && \
    chmod +x /usr/local/bin/containerlab

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/data /app/certs /app/lab_state /app/device_images && \
    touch /app/certs/ldap_ca.crt

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
