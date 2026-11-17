# FRR with all routing daemons pre-enabled — drop-in replacement for
# frrouting/frr:latest that doesn't require a one-time `sed`+restart inside
# each container. Use the resulting image (plabs/frr:latest) as the image_ref
# on the "FRR Router" template.
#
# Build:
#   docker build -t plabs/frr:latest device_images/frr-lab/
#
# Daemons enabled here cover every common lab workload (OSPF, RIP, BGP,
# ISIS, LDP for MPLS, BFD for fast failure detection, PIM for multicast).
# wpa_supplicant with MACsec (MKA, 802.1X-2010) and its macsec_linux driver:
# the kernel does the 802.1AE data plane, wpa_supplicant negotiates the keys —
# a Linux stand-in for a router running MACsec + MKA. Alpine's package is built
# without the macsec_linux driver, so build it here, on the same base image.
FROM frrouting/frr:latest AS wpa
RUN apk add --no-cache build-base linux-headers libnl3-dev openssl-dev wget
RUN wget -q https://w1.fi/releases/wpa_supplicant-2.10.tar.gz -O /tmp/w.tgz && tar xzf /tmp/w.tgz -C /tmp
COPY mka-eapol-da.sh /tmp/
RUN cd /tmp/wpa_supplicant-2.10 && sh /tmp/mka-eapol-da.sh
WORKDIR /tmp/wpa_supplicant-2.10/wpa_supplicant
RUN printf '%s\n' CONFIG_DRIVER_WIRED=y CONFIG_DRIVER_MACSEC_LINUX=y CONFIG_MACSEC=y \
      CONFIG_LIBNL32=y CONFIG_CTRL_IFACE=y CONFIG_IEEE8021X_EAPOL=y CONFIG_TLS=openssl \
      CONFIG_BACKEND=file CONFIG_ELOOP=eloop CONFIG_L2_PACKET=linux > .config \
 && make -j"$(nproc)" wpa_supplicant wpa_cli >/dev/null

FROM frrouting/frr:latest
COPY --from=wpa /tmp/wpa_supplicant-2.10/wpa_supplicant/wpa_supplicant /tmp/wpa_supplicant-2.10/wpa_supplicant/wpa_cli /usr/sbin/
RUN apk add --no-cache libnl3 openssl

RUN set -eux; \
    for d in ospfd ospf6d ripd ripngd bgpd isisd ldpd bfdd pimd nhrpd babeld eigrpd pbrd staticd; do \
      if grep -q "^${d}=no" /etc/frr/daemons; then \
        sed -i "s/^${d}=no/${d}=yes/" /etc/frr/daemons; \
      fi; \
    done; \
    grep '=yes' /etc/frr/daemons
