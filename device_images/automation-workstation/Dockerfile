# Phanero Labs automation workstation — the "student laptop" inside a lab.
# Everything the CCNP Automation (and SP automation) blueprints exercise:
# Python + ncclient / netmiko / scrapli / requests / pygnmi / pyATS-Genie,
# Ansible with the Cisco collections, gnmic, pyang, git.
#   docker build -t plabs/workstation:latest device_images/automation-workstation/
FROM python:3.12-slim-bookworm

ARG GNMIC_VERSION=0.39.1
ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1 \
    ANSIBLE_HOST_KEY_CHECKING=False

RUN apt-get update && apt-get install -y --no-install-recommends \
      bash-completion ca-certificates curl git iproute2 iputils-ping jq less nano vim-tiny \
      openssh-client sshpass telnet traceroute dnsutils tcpdump netcat-openbsd tree tmux \
      build-essential libxml2-dev libxslt1-dev \
    && rm -rf /var/lib/apt/lists/*

RUN pip install \
      ncclient netmiko scrapli scrapli-netconf "scrapli[paramiko]" requests httpx pygnmi \
      "pyats[library]" jinja2 pyyaml textfsm ntc-templates ttp xmltodict lxml rich ipython \
      pyang ansible-core ansible-pylibssh paramiko

RUN ansible-galaxy collection install cisco.ios cisco.iosxr cisco.nxos ansible.netcommon ansible.utils

RUN curl -fsSL "https://github.com/openconfig/gnmic/releases/download/v${GNMIC_VERSION}/gnmic_${GNMIC_VERSION}_Linux_x86_64.tar.gz" \
      | tar -xz -C /usr/local/bin gnmic && chmod +x /usr/local/bin/gnmic

RUN useradd -m -s /bin/bash student && mkdir -p /home/student/work && chown -R student /home/student
COPY motd /etc/motd
RUN echo 'cat /etc/motd' >> /home/student/.bashrc && echo 'cat /etc/motd' >> /root/.bashrc
WORKDIR /home/student/work
CMD ["sleep", "infinity"]
