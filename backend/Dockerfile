# Containerized backend — an alternative to the native systemd install
# (scripts/install-host.sh). It must run privileged with host networking and
# the host Docker socket because it orchestrates containerlab on the host.
FROM python:3.12-slim

WORKDIR /app/backend

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        curl ca-certificates iproute2 iptables jq git make openssh-client telnet && \
    rm -rf /var/lib/apt/lists/*

# Docker CLI only — talks to the host daemon through the mounted socket.
ARG DOCKER_VERSION=27.5.1
RUN curl -fsSL "https://download.docker.com/linux/static/stable/x86_64/docker-${DOCKER_VERSION}.tgz" \
    | tar -xz -C /tmp && cp /tmp/docker/docker /usr/local/bin/docker && rm -rf /tmp/docker

# containerlab >= 0.83 is required: Phanero Labs relies on `deploy` reconciling
# a running lab in place (add/remove nodes + links without a full redeploy).
ARG CLAB_VERSION=0.83.0
RUN curl -fsSL "https://github.com/srl-labs/containerlab/releases/download/v${CLAB_VERSION}/containerlab_${CLAB_VERSION}_linux_amd64.tar.gz" \
    | tar -xz -C /usr/local/bin/ containerlab && chmod +x /usr/local/bin/containerlab

COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt

COPY . .

EXPOSE 8100
CMD ["uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8100"]
