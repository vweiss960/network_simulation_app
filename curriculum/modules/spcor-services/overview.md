## About this module

The Networking module built the routing a service provider's core runs on.
This module builds what it *sells* on top of it:

1. **MPLS and LDP (3.1, 1.5.d)** — a BGP-free core: label switched paths
   between the PEs, verified with MPLS OAM, kept consistent with LDP–IGP sync
   and session protection, secured with authentication and label filtering.
2. **L3VPN (4.3)** — VRFs, route distinguishers and route targets over a
   VPNv4 route reflector for two customers with overlapping addresses; then
   shared services: an extranet to a provider-hosted server and Internet
   access from an Internet VRF, on an IOS XR PE.
3. **Unified MPLS (3.1.d)** — separate IGP domains joined by BGP labeled
   unicast: border routers as inline route reflectors with next-hop-self,
   LSPs stitched from LDP and BGP labels, an L3VPN between PEs that share no
   IGP.
4. **L2VPN and Carrier Ethernet (4.2)** — an E-Line pseudowire with VLAN
   translation, an E-LAN with VPLS, E-Access over an 802.1ad ENNI, and
   Ethernet OAM (802.3ah, CFM); an IOS XR PE joins the E-LAN and builds an
   XR-to-XE E-Line (signalling only: XRd has no L2 data plane); E-Tree and
   802.1ah PBB on XR as configuration and state; G.8032 in the readings.

5. **Multicast (4.4)**: PIM sparse mode with a static RP on IOS XR, IGMPv3,
   SSM, PIM-BIDIR with its designated-forwarder election, BSR, and IPv6
   multicast with PIMv6 and MLDv2, across an IOS XR core with IOS XR and
   IOS XE last-hop routers and real source / receiver traffic.

6. **QoS (4.5)**: classification and marking at the trust boundary, DSCP to
   MPLS EXP, a two-rate three-color policer that marks down, hierarchical
   shaping with LLQ, DSCP-based WRED on IOS XE with real voice and bulk
   flows, and the IOS XR core policy (configuration and verification: XRd
   has no QoS data plane).

More lessons follow, in this order: control plane security (1.5) and data
plane security (1.7).

## How to work through it

- Take the lessons in order: each service runs over the MPLS core the first
  lesson builds.
- The core routers are **IOS XR** (XRd) and the PEs **IOS XE** (CSR 1000v):
  expect to configure every feature in both syntaxes.
- A few blueprint topics need hardware no lab image has (MACsec, BGPsec,
  G.8032 rings) or a data plane XRd lacks (E-Tree and 802.1ah PBB traffic):
  they are covered in the readings, plus configuration and state on XR.

## Before you start

- Finish the **Networking** module first (IS-IS and BGP especially) — this
  module assumes you can build and verify both.
- The lessons need the **Cisco XRd control plane** and **CSR 1000v** images.
