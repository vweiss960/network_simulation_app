## Why service providers run IS-IS

IS-IS and OSPF are both link-state protocols running Dijkstra's SPF over a
database of link states. Most large service providers still pick IS-IS for
the core:

- **It doesn't run over IP.** IS-IS packets ride directly on layer 2 (CLNS),
  so the routing protocol keeps working even when IP addressing is broken, and
  it can't be attacked with spoofed IP packets from off-link.
- **TLVs make it easy to extend.** IPv6, multi-topology, traffic engineering
  and segment routing were added as new TLVs — no new protocol version (OSPF
  needed OSPFv3 for IPv6).
- **One process carries IPv4 and IPv6** — and with multi-topology each family
  gets its own SPF.
- **A simple, flat-friendly hierarchy:** two levels instead of OSPF's area
  types, and many SPs run a single level 2 domain for the whole network.

## Areas and NET addressing

Every IS-IS router has a **NET** (network entity title), an NSAP address
with the selector set to 00:

```
49.0001 . 0000.0000.0001 . 00
 area      system ID       NSEL
```

- **Area ID** — `49` is the AFI for private addressing, followed by the area
  number. Here: `49.0000` (core), `49.0001` and `49.0002` (the PoPs).
- **System ID** — 6 bytes, unique in the whole domain. A common convention
  derives it from the loopback: `10.0.0.1` → `0100.0000.0001`. This lab uses
  the router number.
- **NSEL** — always `00` for a router.

Unlike OSPF, **the area is a property of the router, not of the link**. The
area boundary runs *between* routers, on the links that join different areas.

## The levels

| Level | What it is | Adjacency forms between… |
|---|---|---|
| **Level 1** | intra-area routing | routers in the **same area** |
| **Level 2** | inter-area (backbone) routing | any two L2-capable routers, **any area** |

A router is **level-1**, **level-2-only** or **level-1-2** (the default on
both IOS XR and IOS XE):

- An **L1** router only knows its own area. It reaches everything else
  through the nearest L1/L2 router, whose LSP has the **ATT (attached) bit**
  set — the L1 router installs a default route towards it.
- An **L2** router knows every level 2 prefix in the domain.
- An **L1/L2** router keeps **two LSDBs** and runs SPF twice. It advertises
  its area's level 1 prefixes into level 2 automatically.

The level 2 routers must form one **contiguous backbone** — there's no
"area 0", but the L2 topology plays that role.

## Design patterns: when to use each level

**Level 2 core.** Core (P) routers only connect to other core routers and to
PoP border routers. Make them `is-type level-2-only`: they need no level 1
database, and nothing in level 1 can disturb them.

**L1/L2 at the PoP edge.** The routers that join a PoP to the core are L1/L2.
Make their **core-facing interfaces** `circuit-type level-2-only` and their
**access-facing interfaces** `circuit-type level-1`, so each link runs only
the adjacency it needs (half the hellos, no duplicate adjacencies).

**Level 1 access.** Access / PE routers in a large PoP are level-1-only. They
carry a small LSDB, and a flap inside the PoP stays inside it. Give every PoP
**two** L1/L2 exits for redundancy — and remember that with a default route
only, traffic leaves through the *nearest* exit, not necessarily the best one.

**Leak the loopbacks.** In an MPLS network a default route isn't enough: LDP,
segment routing and BGP next hops need a **/32 (/128) to every PE loopback**.
Leak L2 host routes into L1 (RFC 5302 up/down bit prevents loops) — or don't
use level 1 at all.

**Or stay flat.** Many SPs run the whole network as **one level 2 domain**:
modern routers handle thousands of nodes in one LSDB, and a single level
avoids leaking, sub-optimal exits and two SPF runs. Introduce level 1 when the
domain gets very large, or when a region (e.g. a big metro access ring) should
be isolated.

| Role | is-type | Interfaces |
|---|---|---|
| Core / P | level-2-only | all L2 |
| PoP border / aggregation | level-1-2 | core: `level-2-only`, access: `level-1` |
| Access / PE in an L1 area | level-1 | all L1 |
| Flat design, every router | level-2-only | all L2 |

## Multi-topology

By default IS-IS uses **single topology**: one SPF, whose result is used for
IPv4 *and* IPv6. That's correct only if every link and every router runs both
families. A link that runs IPv4 only — like this lab's R1–R3 backup — becomes
a black hole for IPv6.

**Multi-topology** (RFC 5120) advertises IPv6 reachability and IPv6
adjacencies in their own TLVs (222 / 237) with **MT ID 2**, and runs a
**separate SPF** for IPv6:

- Each topology has its **own metrics**.
- IPv6 traffic only uses links that run IPv6.
- It needs **wide metrics** (`metric-style wide`).

```
! IOS XR — IPv6 is multi-topology by default
!          (single-topology turns it off)
router isis CORE
 address-family ipv6 unicast
  metric-style wide

! IOS XE — single topology by default
router isis CORE
 metric-style wide
 address-family ipv6
  multi-topology
 exit-address-family
```

Use multi-topology whenever IPv6 is not on every link — in practice, always.

## Route advertisement

IS-IS advertises:

- **Connected prefixes of IS-IS interfaces** — including **passive**
  interfaces (loopbacks, customer LANs), which advertise their prefix without
  sending hellos. Best practice: run IS-IS only on infrastructure links and
  advertise loopbacks passively.
- **Redistributed routes** — static, connected, BGP… into level 1, level 2 or
  both. Unfiltered, *every* route of that source is advertised, so **always
  filter** redistribution with a route policy / route map. A default route is
  never redistributed: it's originated with `default-information originate`.
- **Level 1 → level 2:** automatic on every L1/L2 router.
- **Level 2 → level 1:** never by default — only the ATT-bit default — unless
  you configure **route leaking**:

```
! IOS XR — under each address family
router isis CORE
 address-family ipv4 unicast
  propagate level 2 into level 1 route-policy LOOPBACKS-V4

! IOS XE
router isis CORE
 redistribute isis ip level-2 into level-1 route-map LOOPBACKS-V4
 address-family ipv6
  redistribute isis level-2 into level-1 route-map LOOPBACKS-V6
```

Leaked routes appear as **inter-area** (`i ia`) on level 1 routers. Their up/down
bit stops them from being advertised back into level 2.

## Metrics

| | Narrow (default) | Wide |
|---|---|---|
| Per-link metric | 0–63 | 0–16 777 215 |
| Path metric | up to 1023 | up to ~4.26 billion |
| Needed for | — | multi-topology, traffic engineering, segment routing |

- Every interface defaults to **metric 10**, regardless of bandwidth: there
  is no reference bandwidth as in OSPF. Set metrics explicitly — per link,
  from bandwidth or latency.
- A route's metric = the outgoing link metrics along the path + the metric
  the prefix is advertised with. Loopbacks are advertised at **metric 0**
  (IOS XR and IOS XE alike); a connected link prefix at its interface's
  metric.
- With multi-topology, IPv4 and IPv6 metrics are configured **separately**.
- Narrow and wide routers don't understand each other's TLVs; migrate with
  `metric-style transition`, then move everyone to wide.

```
! IOS XR — under the interface's address family
interface GigabitEthernet0/0/0/0
 address-family ipv4 unicast
  metric 100
 address-family ipv6 unicast
  metric 100

! IOS XE — on the interface
interface GigabitEthernet2
 isis metric 100
 isis ipv6 metric 100
```

## IOS XR vs IOS XE configuration

| | IOS XR | IOS XE |
|---|---|---|
| Enable on an interface | `router isis CORE` → `interface X` → `address-family ipv4 unicast` | `ip router isis CORE` / `ipv6 router isis CORE` on the interface |
| Passive | `passive` under the interface | `passive-interface X` under the process |
| Router level | `is-type level-1 / level-2-only` | `is-type level-1 / level-2-only` |
| Interface level | `circuit-type level-1 / level-2-only` | `isis circuit-type level-1 / level-2-only` |
| Point-to-point | `point-to-point` | `isis network point-to-point` |
| Metric | `metric 100` under the interface AF | `isis metric 100` / `isis ipv6 metric 100` |
| IPv6 topology default | multi-topology (`single-topology` to disable) | single topology (`multi-topology` to enable) |
| Leaking | `propagate level 2 into level 1 route-policy` | `redistribute isis ip level-2 into level-1 route-map` |
| Policies | RPL (`route-policy … end-policy`) | prefix lists + route maps |

## Verification commands

| What | IOS XR | IOS XE |
|---|---|---|
| Adjacencies (level, state) | `show isis neighbors` · `show isis adjacency` | `show isis neighbors` |
| Process, levels, topologies | `show isis` · `show isis protocol` | `show isis protocol` · `show clns protocol` |
| The LSDB (ATT bit, TLVs) | `show isis database [detail]` | `show isis database [detail]` |
| SPF results per topology | `show isis topology` · `show isis ipv6 topology` | `show isis topology` · `show isis ipv6 topology` |
| Interface metrics / levels | `show isis interface` | `show isis interface brief` · `show clns interface` |
| IS-IS routes | `show route isis` · `show route ipv6 isis` | `show ip route isis` · `show ipv6 route isis` |

## Wrap-up and exam tips

- NET = area + system ID + `00`; the area belongs to the router.
- L1 adjacencies need the same area; L2 adjacencies don't. L2 must be
  contiguous.
- L1 routers get a default from the ATT bit; L1 → L2 is automatic, L2 → L1
  needs **leaking** — essential for MPLS loopbacks.
- Core: `level-2-only`; PoP border: L1/L2 with `circuit-type` per link;
  access: `level-1`. Or keep the whole network in level 2.
- Multi-topology: separate IPv6 SPF and metrics; requires wide metrics.
- Metrics default to 10 everywhere — set them explicitly.
- IOS XE's IPv6 table shows `I1` / `I2` / `IA` for `i L1` / `i L2` / `i ia`.
