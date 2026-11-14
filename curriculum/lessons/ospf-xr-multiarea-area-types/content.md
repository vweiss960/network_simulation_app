## Why split OSPF into areas?

OSPF is a link-state protocol: every router in an area holds an identical copy
of the **link-state database (LSDB)** and runs SPF over it. That works well
until the network grows — every link flap anywhere triggers SPF everywhere, and
every router stores every link.

**Areas** put a boundary around that work:

- Routers only hold the *detailed* topology of their own area.
- Other areas are described by **summaries** (prefix + cost), not by their links.
- A flap inside area 1 triggers a full SPF only inside area 1; the rest of the
  network just updates a summary.

Every OSPF design has a **backbone, area 0**. All other areas attach to it, and
traffic between non-backbone areas always crosses area 0. The routers that sit
on the boundary — with interfaces in area 0 and another area — are **area border
routers (ABRs)**. A router that brings routes in from outside OSPF
(redistribution) is an **autonomous system boundary router (ASBR)**.

In this lab the backbone is a triangle: R1 and R2 run IOS XR, R3 runs IOS XE.
R1 (XR) is the ABR for area 1, R3 (XE) the ABR for area 2 — so every
interoperability case shows up: XR↔XR, XR↔XE, ABR on each OS.

## Neighbor adjacency

Two OSPF routers on a link become neighbors in stages:

| State | What happened |
|---|---|
| `DOWN` | No hello heard yet |
| `INIT` | A hello arrived, but it doesn't list *our* router ID yet |
| `2-WAY` | Each side sees itself in the other's hello (DROTHERs stop here on broadcast links) |
| `EXSTART` / `EXCHANGE` | Master/slave chosen, database descriptions exchanged |
| `LOADING` | Missing LSAs requested |
| `FULL` | LSDBs synchronised — routes can be calculated |

The hello carries parameters that **must match** or the routers never get past
`INIT` (or silently ignore each other):

- **Area ID** and **area type** (stub / NSSA flags)
- **Hello and dead intervals** — XR and XE both default to 10 / 40 s, so a
  timer set on one side only breaks the adjacency
- **Authentication**
- **Subnet and mask** (OSPFv2 on broadcast links)

Two more things can stall an adjacency later:

- **MTU** mismatch → stuck in `EXSTART` / `EXCHANGE`. Note that XR's interface
  `mtu` includes the layer-2 header (1514) while XE's `ip mtu` does not (1500).
- **Network type** mismatch (point-to-point vs broadcast) → the adjacency may
  reach `FULL`, but routes are missing because one side waits for a DR. Set
  `point-to-point` on **both** ends of a router-to-router link.

## OSPFv3 — what changes for IPv6

OSPFv3 is the same protocol design — areas, SPF, the same area types and the
same adjacency states — with a few important differences:

| | OSPFv2 | OSPFv3 |
|---|---|---|
| Carries | IPv4 | IPv6 (and IPv4, with address families) |
| Runs over | IPv4 (protocol 89) | IPv6, from **link-local** addresses (FF02::5 / FF02::6) |
| Router ID | 32-bit, often taken from an IPv4 address | still 32-bit dotted — set it explicitly on an IPv6-only router |
| Neighbors identified by | interface address on broadcast links | **router ID** always |
| Prefixes carried in | Router / network LSAs | separate **link (type 8)** and **intra-area-prefix (type 9)** LSAs |
| Authentication | in the OSPF header | IPsec / authentication trailer |

The practical consequence: OSPFv2 and OSPFv3 are **two separate processes**
with separate adjacencies, LSDBs and configuration. Everything you configure
for one — areas, area types, summaries, redistribution, costs — must be done
again for the other.

## LSA types you'll see in this lab

| OSPFv2 type | OSPFv3 equivalent | Originated by | Flooded | In the RIB |
|---|---|---|---|---|
| 1 Router | 0x2001 Router | every router | its area | `O` |
| 2 Network | 0x2002 Network | the DR | its area | `O` |
| 3 Summary | 0x2003 Inter-area-prefix | ABR | other areas | `O IA` (XE IPv6: `OI`) |
| 4 ASBR summary | 0x2004 Inter-area-router | ABR | other areas | (how to reach the ASBR) |
| 5 External | 0x4005 AS-external | ASBR | everywhere except stub / NSSA areas | `O E1` / `O E2` |
| 7 NSSA external | 0x2007 NSSA | ASBR inside an NSSA | that NSSA | `O N1` / `O N2` |
| — | 0x0008 Link | every router | the link only | (link-local next hops) |
| — | 0x2009 Intra-area-prefix | every router / DR | its area | `O` |

- Type 3 LSAs make a route **inter-area**. ABRs create them from the routes
  they know in one area and flood them into the others.
- External routes are **E2 by default**: the metric is the one set at
  redistribution (20) and doesn't grow across the network. E1 adds the
  internal cost to reach the ASBR.

## Multi-area addressing and summarisation

Areas only reduce the work if the ABRs can **summarise**. That depends on the
addressing plan: give each area a contiguous block, and the ABR can advertise
one inter-area route for the whole area.

| Area | IPv4 block | IPv6 block |
|---|---|---|
| 0 | 10.0.0.0/16 + loopbacks | 2001:db8::/48 |
| 1 | 10.1.0.0/16 | 2001:db8:1::/48 |
| 2 | 10.2.0.0/16 | 2001:db8:2::/48 |

The ABR configures the summary with **`range`** for the area it summarises:

```
! IOS XR (both processes look the same)
router ospf 1
 area 1
  range 10.1.0.0/16
router ospfv3 1
 area 1
  range 2001:db8:1::/48

! IOS XE
router ospf 1
 area 2 range 10.2.0.0 255.255.0.0
router ospfv3 1
 address-family ipv6 unicast
  area 2 range 2001:DB8:2::/48
```

- The summary is advertised as long as at least one route inside the range
  exists in the area; its cost is derived from the component routes' costs
  (RFC 2328 uses the highest; RFC 1583-compatible behaviour the lowest).
- The ABR installs a **discard route** (to Null0) for the summary, so traffic
  to an unused part of the block is dropped instead of looping.
- `range … not-advertise` hides the block entirely — a crude filter.
- External routes are summarised at the **ASBR** instead (`summary-prefix`).

## Area types

| Area type | Inter-area | External (type 5) | Default from ABR | ASBR allowed |
|---|---|---|---|---|
| Normal | yes | yes | no | yes |
| **Stub** | yes | **no** | **yes** | no |
| **Totally stubby** | **no** (default only) | **no** | **yes** | no |
| **NSSA** | yes | **no** | optional | **yes** (as type 7) |
| Totally NSSA | no (default only) | no | yes | yes |

- **Stub** removes external routes from an area with one way out.
- **Totally stubby** also removes inter-area routes. It's configured on the ABR
  alone (`stub no-summary`); the other routers in the area are plain stub.
- **NSSA** keeps the backbone's externals out but allows a *local* ASBR. It
  originates **type 7** LSAs, and the ABR **translates** them into type 5.
- The stub / NSSA flag is part of the hello: **every router in the area must
  agree**, or adjacencies drop.

## Metrics

OSPF's metric is the sum of the outgoing interface **costs** along the path.
By default:

> cost = reference bandwidth ÷ interface bandwidth (minimum 1)

Both IOS XR and IOS XE use a **100 Mb/s** reference, which makes every link of
100 Mb/s and above cost 1 — a 1 G, a 10 G and a 100 G link look identical.
Two ways to fix that:

- **Raise the reference bandwidth** — `auto-cost reference-bandwidth 100000`
  (Mb/s, i.e. 100 G) — on **every router, in both processes**. A router left
  at the old reference computes different costs and skews every path through
  it; XE even warns you to keep it consistent.
- **Set the cost per interface** — to express policy (a backup link, an
  expensive leased line) rather than raw bandwidth.

```
! IOS XR — cost under the area's interface
router ospf 1
 auto-cost reference-bandwidth 100000
 area 0
  interface GigabitEthernet0/0/0/2
   cost 1000

! IOS XE — cost on the interface itself
router ospf 1
 auto-cost reference-bandwidth 100000
interface GigabitEthernet3
 ip ospf cost 1000
 ospfv3 cost 1000
```

The default route an ABR sends into a stub area has its own cost
(`default-cost`, 1 by default), added to the cost of the link towards it.

## IOS XR vs IOS XE configuration

| | IOS XR | IOS XE |
|---|---|---|
| Enable on an interface | under `router ospf 1` → `area 0` → `interface X` | `ip ospf 1 area 0` on the interface (or `network … area 0`) |
| OSPFv3 | `router ospfv3 1` → `area 0` → `interface X` | `router ospfv3 1` + `address-family ipv6 unicast`; `ospfv3 1 ipv6 area 0` on the interface |
| Passive | `passive enable` (v2) / `passive` (v3), per interface | `passive-interface X` under the process (v3: under the AF) |
| Point-to-point | `network point-to-point` under the interface | `ip ospf network point-to-point` / `ospfv3 network point-to-point` |
| Area type | `area 1` → `stub` / `stub no-summary` / `nssa` | `area 1 stub` / `area 1 stub no-summary` / `area 2 nssa` |
| Applying changes | nothing happens until `commit` | immediate |

## Verification commands

| What | IOS XR | IOS XE |
|---|---|---|
| Neighbors | `show ospf neighbor` · `show ospfv3 neighbor` | `show ip ospf neighbor` · `show ospfv3 neighbor` |
| Process (ABR / ASBR, areas) | `show ospf` · `show ospfv3` | `show ip ospf` · `show ospfv3` |
| Interfaces, costs, timers | `show ospf interface brief` | `show ip ospf interface brief` |
| OSPF routes | `show route ospf` · `show route ipv6 ospf` | `show ip route ospf` · `show ipv6 route ospf` |
| The LSDB | `show ospf database` · `show ospfv3 database` | `show ip ospf database` · `show ospfv3 database` |

## Wrap-up and exam tips

- Hello / dead timers, area ID, area type and authentication must match;
  MTU and network type mismatches show up later.
- OSPFv3 is a separate process: every design decision is configured twice.
- ABRs generate type 3 / 4 LSAs and summarise with `range`; ASBRs generate
  type 5 — or type 7 in an NSSA, which the ABR translates.
- Stub → no externals; totally stubby → no externals and no inter-area routes
  (ABR only); NSSA → no backbone externals, local redistribution allowed.
- Raise the reference bandwidth on **every** router, in both processes.
- XE shows IPv6 inter-area routes as `OI`; XR shows `O IA` in both tables.
