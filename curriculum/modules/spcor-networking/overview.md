## About this module

Every service provider network starts with its **interior gateway protocol**.
The IGP carries the loopbacks and infrastructure links that everything else
depends on: BGP next hops, MPLS label switched paths, segment routing and
fast reroute all assume the IGP has converged on a correct, loop-free view of
the network.

This module follows the CCNP SPCOR blueprint's Networking section:

1. **IS-IS (2.1)** — the IGP most large service providers run: NET addressing,
   level 1 / level 1-2 / level 2 design, multi-topology IPv6, route
   advertisement and leaking, and metrics.
2. **OSPF (2.2)** — OSPFv2 and OSPFv3: adjacencies, route advertisement,
   multi-area design with summarisation and stub / NSSA areas, and metrics.
3. **BGP in the core (2.4 a, b, c, f)** — iBGP over the IGP through a route
   reflector, eBGP to a customer and two upstreams, IPv4 and IPv6 address
   families, originating and redistributing prefixes.
4. **Path selection and policy (2.3, 2.4 d, e, 2.5)** — the best-path
   algorithm, local preference, AS-path prepending and communities, written
   as IOS XR route policies and IOS XE route maps — for BGP and IS-IS.
5. **Additional paths and PIC (2.4 g, h)** — getting backup paths past the
   route reflector and pre-installing them for fast failover.

6. **Troubleshooting (2.6)** — two scenarios, IS-IS + BGP and OSPF: Chaos
   breaks a working network with faults from each protocol's usual suspects,
   and you work the tickets with show commands until it's healthy again. A
   scenario counts as done once you've solved a round; play as many as you like.

The BGP lessons build on each other: lessons 4 and 5 start from the network
lesson 3 builds. The troubleshooting scenarios use the same networks, fully
built.

## How to work through it

- Each lesson is a lab of its own: start it, deploy the lab, and work through
  the tasks in order. **Check** grades each task on the live devices.
- The IGP lessons use the **same topology** — a three-router core triangle
  with an access area on each side — so you can compare how the two protocols
  solve the same problems. The BGP lessons reuse that core, now running
  IS-IS, with a customer and two upstream providers around it.
- Every lesson mixes **IOS XR** (XRd) and **IOS XE** (CSR 1000v) routers:
  expect to configure each feature in both syntaxes, and to verify it with
  each OS's commands.
- Hints are available on request; the AI assistant can look at your lab and
  nudge you, but won't configure it for you.

## Before you start

- The lessons need the **Cisco XRd control plane** and **CSR 1000v** images
  (see the Images page). The CSR routers take about five minutes to boot.
- The IGP labs use five routers and two hosts (roughly 16 GB of RAM); the
  BGP labs six routers (roughly 20 GB).
- Allow about **two hours per lesson**. You can stop a lab and come back: your
  progress is kept.
