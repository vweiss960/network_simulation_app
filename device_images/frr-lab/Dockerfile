# FRR with all routing daemons pre-enabled — drop-in replacement for
# frrouting/frr:latest that doesn't require a one-time `sed`+restart inside
# each container. Use the resulting image (frr-lab:latest) as the image_ref
# on Phanero's "FRR Router (lab)" template.
#
# Build:
#   docker build -t frr-lab:latest device_images/frr-lab/
#
# Daemons enabled here cover every common lab workload (OSPF, RIP, BGP,
# ISIS, LDP for MPLS, BFD for fast failure detection, PIM for multicast).
FROM frrouting/frr:latest

RUN set -eux; \
    for d in ospfd ospf6d ripd ripngd bgpd isisd ldpd bfdd pimd nhrpd babeld eigrpd pbrd staticd; do \
      if grep -q "^${d}=no" /etc/frr/daemons; then \
        sed -i "s/^${d}=no/${d}=yes/" /etc/frr/daemons; \
      fi; \
    done; \
    grep '=yes' /etc/frr/daemons
