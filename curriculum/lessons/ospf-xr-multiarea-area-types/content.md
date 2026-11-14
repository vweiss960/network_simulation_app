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

## LSA types you'll see in this lab

| Type | Name | Originated by | Flooded | Shows in the RIB as |
|---|---|---|---|---|
| 1 | Router LSA | every router | its own area | `O` |
| 2 | Network LSA | the DR on a broadcast segment | its own area | `O` |
| 3 | Summary LSA | ABR | into other areas | `O IA` |
| 4 | ASBR summary LSA | ABR | into other areas | (how to reach the ASBR) |
| 5 | External LSA | ASBR | everywhere except stub / NSSA areas | `O E1` / `O E2` |
| 7 | NSSA external LSA | ASBR inside an NSSA | that NSSA only | `O N1` / `O N2` |

Two things worth remembering:

- Type 3 LSAs are what make a route **inter-area** (`O IA`). They're created by
  the ABR from the routes it knows in one area, and re-flooded into the others.
- External routes are **E2 by default**: the metric is the one set at
  redistribution and doesn't grow as the route crosses the network. E1 adds the
  internal cost to reach the ASBR.

## Area types

| Area type | Type 3 (inter-area) | Type 5 (external) | Default route from ABR | Can contain an ASBR |
|---|---|---|---|---|
| Normal | yes | yes | no | yes |
| **Stub** | yes | **no** | **yes** | no |
| **Totally stubby** | **no** (default only) | **no** | **yes** | no |
| **NSSA** | yes | **no** | optional | **yes** (as type 7) |
| Totally NSSA | no (default only) | no | yes | yes |

- **Stub** removes external routes from an area that has only one way out —
  the ABR's default route covers them.
- **Totally stubby** also removes inter-area routes. It's configured on the ABR
  alone (`stub no-summary`); the other routers in the area are plain stub.
- **NSSA** is for an area that must stay free of the backbone's external routes
  but has its *own* external connection. The local ASBR originates **type 7**
  LSAs, and the ABR **translates** them into type 5 for the rest of the domain.
- The stub / NSSA flag is part of the hello: **every router in the area must
  agree**, or adjacencies won't form.

## OSPF on IOS XR

IOS XR configures OSPF **per interface, under the area** — there are no
`network … area …` statements, and nothing takes effect until you `commit`:

```
router ospf 1
 router-id 1.1.1.1
 area 0
  interface Loopback0
   passive enable
  !
  interface GigabitEthernet0/0/0/0
   network point-to-point
  !
 !
 area 1
  stub no-summary
  interface GigabitEthernet0/0/0/1
   network point-to-point
  !
 !
!
```

- `passive enable` advertises a prefix without sending hellos on it — use it on
  loopbacks and host LANs.
- `network point-to-point` skips the DR/BDR election on a link with exactly two
  routers, so adjacencies come up faster.
- Area options (`stub`, `stub no-summary`, `nssa`) sit directly under the area.
- Redistribution is a process-level command: `redistribute static`,
  `redistribute connected`, optionally with `metric-type 1` or a `route-policy`.

## Verification commands

| What | Command |
|---|---|
| Neighbors and their state | `show ospf neighbor` |
| Process overview (ABR / ASBR, areas, area types) | `show ospf` |
| OSPF routes in the RIB | `show route ospf` |
| One prefix in detail | `show route 203.0.113.0/24` |
| The LSDB, per LSA type | `show ospf database` · `show ospf database summary` · `show ospf database external` · `show ospf database nssa-external` |
| Interfaces in OSPF | `show ospf interface brief` |

A healthy adjacency is `FULL`. Stuck in `INIT` or `EXSTART` usually means a
mismatch (area, area type, MTU, authentication or network type) between the two
ends.

## Wrap-up and exam tips

- Area 0 is mandatory and every other area must touch it.
- ABRs generate type 3 (and type 4) LSAs; ASBRs generate type 5 — or type 7 in
  an NSSA, which the ABR translates.
- Stub → no externals; totally stubby → no externals and no inter-area routes
  (configured only on the ABR); NSSA → no backbone externals, but local
  redistribution allowed.
- An area-type mismatch silently prevents the adjacency: check both ends first.
- On IOS XR, remember **commit** — and `show configuration failed` if a commit
  is rejected.
