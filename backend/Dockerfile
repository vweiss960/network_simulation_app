# Containerized backend — an alternative to the native systemd install
# (scripts/install-host.sh). It must run privileged with host networking and
# the host Docker socket because it orchestrates containerlab on the host.
FROM python:3.12-slim

WORKDIR /app/backend

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        curl ca-certificates iproute2 iptables iputils-ping jq git make openssh-client telnet && \
    rm -rf /var/lib/apt/lists/*

# Docker CLI only — talks to the host daemon through the mounted socket.
ARG DOCKER_VERSION=27.5.1
RUN curl -fsSL "https://download.docker.com/linux/static/stable/x86_64/docker-${DOCKER_VERSION}.tgz" \
    | tar -xz -C /tmp && cp /tmp/docker/docker /usr/local/bin/docker && rm -rf /tmp/docker

# buildx, so `docker build` uses BuildKit. vrnetlab Dockerfiles rely on it:
# e.g. cat9kv's `COPY vswitch.xm[l] …` (an optional file) fails on the
# legacy builder with "COPY failed: no source files were specified".
ARG BUILDX_VERSION=0.19.3
RUN mkdir -p /usr/local/lib/docker/cli-plugins && \
    curl -fsSL -o /usr/local/lib/docker/cli-plugins/docker-buildx \
      "https://github.com/docker/buildx/releases/download/v${BUILDX_VERSION}/buildx-v${BUILDX_VERSION}.linux-amd64" && \
    chmod +x /usr/local/lib/docker/cli-plugins/docker-buildx
ENV DOCKER_BUILDKIT=1

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
