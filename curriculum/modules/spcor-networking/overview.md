## About this module

Every service provider network starts with its **interior gateway protocol**.
The IGP carries the loopbacks and infrastructure links that everything else
depends on: BGP next hops, MPLS label switched paths, segment routing and
fast reroute all assume the IGP has converged on a correct, loop-free view of
the network.

This module covers the two link-state IGPs in the order of the CCNP SPCOR
blueprint:

1. **IS-IS (2.1)** — the IGP most large service providers run: NET addressing,
   level 1 / level 1-2 / level 2 design, multi-topology IPv6, route
   advertisement and leaking, and metrics.
2. **OSPF (2.2)** — OSPFv2 and OSPFv3: adjacencies, route advertisement,
   multi-area design with summarisation and stub / NSSA areas, and metrics.

## How to work through it

- Each lesson is a lab of its own: start it, deploy the lab, and work through
  the tasks in order. **Check** grades each task on the live devices.
- Both lessons use the **same topology** — a three-router core triangle with
  an access area on each side — so you can compare how the two protocols solve
  the same problems.
- Every lesson mixes **IOS XR** (XRd) and **IOS XE** (CSR 1000v) routers:
  expect to configure each feature in both syntaxes, and to verify it with
  each OS's commands.
- Hints are available on request; the AI assistant can look at your lab and
  nudge you, but won't configure it for you.

## Before you start

- The lessons need the **Cisco XRd control plane** and **CSR 1000v** images
  (see the Images page). The CSR routers take about five minutes to boot.
- Each lab uses five routers and two hosts; plan for roughly 16 GB of RAM per
  running lesson.
- Allow about **two hours per lesson**. You can stop a lab and come back: your
  progress is kept.
