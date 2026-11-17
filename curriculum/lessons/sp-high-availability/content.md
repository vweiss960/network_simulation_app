## Availability in a service provider network

A service provider sells *uptime*. Failures will happen — fibres get cut,
optics die, software crashes, routers get upgraded — so the network is built
to survive them with as little traffic loss as possible. The tools fall into
three groups:

| Problem | Tool | What it buys |
|---|---|---|
| One link isn't enough (capacity, a single point of failure) | **Link aggregation** (LACP bundles) | More bandwidth; a member failure costs capacity, not the link |
| Failures are noticed too late | **BFD** | Sub-second detection, for every routing protocol at once |
| The control plane restarts (crash, upgrade, RP failover) | **NSF / graceful restart**, **NSR** | Forwarding continues while the routing process recovers |

The goal of all three is the same: keep the *forwarding* plane working while
something else is failing or recovering.

## Link aggregation (802.3ad / LACP)

A **bundle** (IOS XR: *Bundle-Ether*; IOS XE: *Port-channel* / EtherChannel)
turns several physical links into one logical interface. Routing protocols
see one adjacency, one IP subnet; traffic is spread over the members by a
hash of the flow (addresses, ports) so a single flow stays on one member and
its packets stay in order.

**LACP** (Link Aggregation Control Protocol, IEEE 802.3ad / 802.1AX)
negotiates the bundle: each end sends LACPDUs on every member and only uses a
member when both sides agree it belongs to the same bundle. That protects
against miscabling — a member that goes to the wrong router never joins.

| Mode | IOS XR | IOS XE | Behaviour |
|---|---|---|---|
| Active | `bundle id N mode active` | `channel-group N mode active` | Sends LACPDUs |
| Passive | `bundle id N mode passive` | `channel-group N mode passive` | Only answers (at least one end must be active) |
| Static | `bundle id N mode on` | `channel-group N mode on` | No LACP at all — no protection |

**LACP state flags** (`show lacp` on XR): `a` aggregatable, `s` synchronized,
`c` collecting, `d` distributing, `A` active mode, `F` fast rate requested,
`D` partner info defaulted (nothing heard), `E` partner info expired. A
healthy member is `ascdA`.

**LACP rate.** By default LACPDUs are sent every 30 s and a partner is
declared gone after 90 s of silence — far too slow when the physical link
stays up but the far end is dead (a media converter, a transport network).
**Fast** rate sends them every second (3 s timeout). The rate is a *request to
the partner*: XR `lacp period short`, XE `lacp rate fast`, on each member.

**Minimum links.** A bundle stays up while at least *N* members are active
(XR `bundle minimum-active links N`, XE `lacp min-bundle N`). With N = 1 a
2 × 10G bundle that loses a member keeps attracting traffic for 20G with 10G
of capacity; with N = 2 it goes down and the IGP reroutes around it.

```
! IOS XR                                  ! IOS XE
interface Bundle-Ether23                  interface Port-channel1
 ipv4 address 10.0.23.1/30                 ip address 10.0.23.2 255.255.255.252
 bundle minimum-active links 2             lacp min-bundle 2
interface GigabitEthernet0/0/0/2          interface GigabitEthernet2
 bundle id 23 mode active                  channel-group 1 mode active
 lacp period short                         lacp rate fast
```

Verify: XR `show bundle bundle-ether 23`, `show lacp bundle-ether 23`; XE
`show etherchannel summary` (`Po1(RU)` = layer 3, in use; members `(bndl)`),
`show lacp neighbor`.

## BFD — Bidirectional Forwarding Detection

Routing protocols detect a dead neighbor with their own hellos, slowly:

| Protocol | Default detection |
|---|---|
| IS-IS | 30 s hold time (10 s hellos × 3) |
| OSPF | 40 s dead interval |
| BGP | 180 s hold time |

Tuning every protocol's timers down costs CPU and doesn't scale. **BFD**
(RFC 5880/5881) is one tiny hello protocol (UDP 3784) whose only job is to
check the *forwarding path* between two neighbors, every few hundred
milliseconds or less. Its clients — IS-IS, OSPF, BGP, static routes, HSRP… —
register with it and are told immediately when a session goes down.

- **Detection time** = negotiated interval × multiplier: 300 ms × 3 = 900 ms.
  Hardware-assisted implementations go down to 3.3 ms × 3 (50 ms failover).
- **Echo mode** (IOS XE default): the router sends packets addressed to
  itself that the neighbor just forwards back, testing the forwarding plane
  without involving the neighbor's CPU; control packets then slow down to
  the *slow timer*.
- **Single-hop** BFD protects a link (IGP neighbors, eBGP); **multihop** BFD
  protects a path (iBGP between loopbacks).

```
! IOS XE                                  ! IOS XR (ASR 9000 / NCS, not XRd)
interface GigabitEthernet4                router isis CORE
 bfd interval 300 min_rx 300 multiplier 3  interface GigabitEthernet0/0/0/2
 isis bfd                                    bfd minimum-interval 300
!                                            bfd multiplier 3
router bgp 65000                             bfd fast-detect ipv4
 neighbor 192.0.2.2 fall-over bfd         !
!                                         router bgp 65000
ip route static bfd Gi2 192.0.2.2         neighbor 192.0.2.2
                                            bfd fast-detect
                                            bfd minimum-interval 300
```

On a bundle, IOS XR runs **BFD over bundle** members (BoB, one session per
member: `bfd address-family ipv4 fast-detect` under the Bundle-Ether) or over
the logical bundle (BLB, which needs `bfd multipath include location …`).

Verify: XE `show bfd neighbors [details]` (state Up/Up, *Registered
protocols*), `show logging | include BFD`; XR `show bfd session`.

**Why BFD isn't configured on the XR routers in this lab:** XRd Control
Plane has no line card, and IOS XR hosts BFD sessions on line cards — the
session is created but shows *DOWN* with *MP download state:
BFD_MP_DOWNLOAD_NO_LC*. A real router has the hardware.

## Graceful restart and NSF

A router's **control plane** (routing processes, on the route processor) and
**forwarding plane** (the FIB, on line cards) are separate. When a routing
process restarts, the FIB is still there and still correct — the danger is
the *neighbors*, who see the session drop and route around a router that is
forwarding perfectly well.

**Graceful restart** lets the neighbors help:

1. Both ends announce the capability when the session starts (BGP: the GR
   capability in the OPEN message, with a *restart time*; IS-IS: the restart
   TLV in hellos; OSPF: opaque grace LSAs).
2. When the restarting router's session drops, its neighbors (the
   **helpers**) keep its routes, marked **stale**, and keep forwarding.
3. The restarted router re-establishes the session, its neighbors send it
   their routes (BGP: followed by an *End-of-RIB* marker), it recomputes and
   sends its own routes; the helpers replace the stale ones and flush what
   wasn't refreshed (BGP: *stale path timeout*).

Cisco calls the IGP version **NSF** (nonstop forwarding): `nsf ietf`
(standard, RFC 5306 for IS-IS) or `nsf cisco` (proprietary) under `router
isis` / `router ospf`. For BGP it's `bgp graceful-restart`.

```
! IOS XR                                  ! IOS XE
router isis CORE                          router isis CORE
 nsf ietf                                  nsf ietf
router bgp 65000                          router bgp 65000
 bgp graceful-restart                      bgp graceful-restart
```

The capability is only negotiated **when a session comes up** — enabling GR
on an established session changes nothing until it's reset.

Verify: XE `show ip bgp neighbors X | include Graceful` (*advertised and
received*), `show isis nsf`; XR `show bgp neighbor X` (*Graceful Restart
capability advertised / received*), `show isis protocol`. During a restart:
XE `show ip bgp` marks the helper's copies **(stale)**.

Exercise it on IOS XR with `process restart bgp` / `process restart isis`.

## NSR — nonstop routing

Graceful restart has two weaknesses: every neighbor must support it, and a
real topology change *during* the restart isn't handled well (stale routes
may point the wrong way). **NSR** (nonstop routing) removes the neighbors
from the picture:

- The routing process runs on **both route processors**; the standby copy is
  kept in sync with the active one — neighbors, TCP state (sequence numbers),
  received and sent routes, the link-state database.
- On an RP failover (or a process failover to the standby) the standby takes
  over the **existing sessions**. The neighbors see nothing: no session reset,
  no stale routes, no GR capability needed.

| | Graceful restart / NSF | NSR |
|---|---|---|
| Needs the neighbors to help | Yes (GR helper) | No |
| Sessions reset | Yes | No |
| Needs a standby RP / process | No | Yes |
| Typical use | Fallback, single-RP routers | Dual-RP SP routers |

On IOS XR: `nsr` under `router isis` / `router ospf`; BGP NSR is enabled by
default on recent releases (`nsr disable` to turn it off); `show bgp summary`
reports *Non-stop routing is enabled* and the *NSR initial initsync* state
(*Reached* once the standby is in sync). On IOS XE, the equivalent for dual-RP
platforms (ASR 1000) is SSO plus BGP NSR (`neighbor X ha-mode sso`).

XRd has a single RP, so in this lab NSR can be configured and inspected, but
a failover can't be demonstrated — a `process restart` falls back to graceful
restart.

## Verification cheat sheet

| What | IOS XR | IOS XE |
|---|---|---|
| Bundle state | `show bundle bundle-ether N` | `show etherchannel summary` |
| LACP | `show lacp bundle-ether N` | `show lacp neighbor` |
| BFD | `show bfd session` | `show bfd neighbors [details]` |
| BGP GR | `show bgp neighbor X` | `show ip bgp neighbors X \| include Graceful` |
| Stale routes | `show bgp` | `show ip bgp` (stale) |
| IS-IS NSF | `show isis protocol` | `show isis nsf` |
| NSR | `show bgp summary`, `show bgp nsr` | — (dual-RP platforms only) |
| Restart a process | `process restart bgp` / `isis` | — (needs a standby RP: `redundancy force-switchover`) |
