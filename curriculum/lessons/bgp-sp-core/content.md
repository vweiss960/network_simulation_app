## BGP's job in a provider network

A service provider runs **two** routing protocols with very different jobs:

| | IGP (IS-IS / OSPF) | BGP |
|---|---|---|
| Carries | infrastructure: loopbacks, core links | customer and Internet prefixes — up to a million |
| Converges on | the topology (SPF) | policy (path attributes) |
| Scope | inside the AS | between ASes (eBGP) and across the AS (iBGP) |

The IGP's only job is to make every **BGP next hop** — every router
loopback — reachable. BGP rides on top of it: an iBGP route is usable only if
its next hop resolves through the IGP. Keep them separate: never redistribute
the full table into the IGP, nor the IGP into BGP.

## iBGP and eBGP

| | eBGP | iBGP |
|---|---|---|
| Between | different ASes | routers of the same AS |
| Peering address | usually the directly connected interfaces | **loopbacks** (`update-source Loopback0`) |
| TTL | 1 (neighbors must be adjacent) | 255 |
| Next hop | set to itself | **unchanged** — use `next-hop-self` at the edge |
| AS path | prepends its own AS | unchanged |
| Loop prevention | drops routes containing its own AS | doesn't advertise iBGP routes to iBGP peers |

**Why loopbacks for iBGP?** A loopback never goes down. As long as the IGP
finds *any* path between two routers, the session stays up — no single link
failure can drop it.

**Why next-hop-self?** An eBGP route arrives with the neighbor's interface
address as next hop. That address isn't in the IGP, so other routers in the
AS can't resolve it and won't install the route. `next-hop-self` on the
iBGP session makes the edge router advertise its own loopback instead. (The
alternative — putting the edge links into the IGP as passive interfaces —
works too, but grows the IGP with every peering link.)

## Route reflectors

Because iBGP routers don't re-advertise iBGP routes to other iBGP peers,
every router needs a session to every other router: a **full mesh**,
n(n−1)/2 sessions — 4 950 for 100 routers.

A **route reflector** (RFC 4456) breaks that rule in a controlled way:

- Its **clients** each peer only with the reflector(s).
- A route from a client is reflected to all other clients and non-clients;
  a route from a non-client is reflected to the clients.
- Two attributes prevent loops: **ORIGINATOR_ID** (a router drops a route it
  originated) and **CLUSTER_LIST** (a reflector drops a route carrying its
  own cluster ID).

Production networks run **two** reflectors per cluster for redundancy — often
dedicated control-plane routers that sit outside the forwarding path, such as
IOS XRd or XRv 9000. One trade-off: a reflector advertises only its **best**
path, which hides alternatives from the clients (the add-paths lesson fixes
that).

## Address families

Multiprotocol BGP (RFC 4760) carries any number of **address families** over
BGP sessions: IPv4 unicast, IPv6 unicast, VPNv4, EVPN, flowspec… Each is
negotiated per session and has its own table.

Two common designs for IPv6:

- **Separate sessions** — IPv4 unicast over an IPv4 session, IPv6 unicast
  over an IPv6 session (this lab). Each family fails independently, and the
  IPv6 next hops are native IPv6 addresses.
- **One session** carrying both families. The IPv6 routes then need an IPv6
  next hop; over an IPv4 session they get an IPv4-mapped one
  (`::ffff:1.1.1.1`), which you must fix with a route policy.

```
! IOS XR — families enabled under the process, then per neighbor
router bgp 65000
 address-family ipv4 unicast
 address-family ipv6 unicast
 neighbor 2001:db8::2
  remote-as 65000
  update-source Loopback0
  address-family ipv6 unicast

! IOS XE — a neighbor is only active in the families you activate it in
router bgp 65000
 no bgp default ipv4-unicast
 neighbor 2001:DB8::2 remote-as 65000
 address-family ipv6
  neighbor 2001:DB8::2 activate
```

`no bgp default ipv4-unicast` stops XE from activating every neighbor for
IPv4 automatically — otherwise your IPv6 neighbors would try to carry IPv4
too.

## eBGP on IOS XR needs a policy

IOS XR implements **RFC 8212**: an eBGP session with no route policy in a
direction accepts or advertises **nothing** in that direction. The session
comes up and prefixes arrive, but none are accepted and none are sent — a
classic "the session is up but I have no routes". Attach a policy in both
directions:

```
route-policy PASS
  pass
end-policy
router bgp 65000
 neighbor 10.3.16.2
  remote-as 65300
  address-family ipv4 unicast
   route-policy PASS in
   route-policy PASS out
```

A pass-everything policy is fine for a first lab; in production every eBGP
session gets real filters (the policy lesson builds them). IOS XE has no such
default — it accepts and advertises everything unless you filter.

## Originating prefixes

Three ways to put a prefix into BGP:

| Method | Origin | Use it for |
|---|---|---|
| `network` statement | IGP (`i`) | your own blocks — the prefix must be in the RIB with that exact mask |
| `aggregate-address` | IGP | a summary of more-specific BGP routes you already have |
| `redistribute` (static, connected…) | incomplete (`?`) | statically routed customers — always through a policy |

The classic pattern for a provider's own block: a **static route to Null0**
for the aggregate plus a `network` statement — on more than one edge router.
The Null0 route keeps the prefix in the RIB (and so in BGP) no matter which
internal subnets exist, and drops traffic to unused space instead of
looping it.

## Verification commands

| What | IOS XR | IOS XE |
|---|---|---|
| Sessions per family | `show bgp ipv4 unicast summary` · `show bgp ipv6 unicast summary` | same |
| One neighbor in detail | `show bgp neighbor 10.3.16.2` | `show bgp ipv4 unicast neighbors 10.2.35.2` |
| The BGP table | `show bgp ipv4 unicast` | `show bgp ipv4 unicast` |
| One prefix, every path | `show bgp ipv4 unicast 100.64.0.0/16` | `show bgp ipv4 unicast 100.64.0.0/16` |
| What a neighbor sent / was sent | `show bgp neighbor X routes` · `advertised-routes` | `show bgp ipv4 unicast neighbors X routes` · `advertised-routes` |
| Installed routes | `show route bgp` | `show ip route bgp` · `show ipv6 route bgp` |

In the BGP table, `*` = valid, `>` = best, `i` (left) = learned from iBGP; the
origin code (`i`, `e`, `?`) is the last character of the line.

## Wrap-up and exam tips

- iBGP between loopbacks over the IGP; eBGP between connected interfaces.
- `next-hop-self` at the edge, or the eBGP next hops must be in the IGP.
- Route reflectors replace the full mesh; ORIGINATOR_ID and CLUSTER_LIST stop
  loops; reflectors only advertise their best path.
- IOS XR: no route policy on an eBGP session = nothing in or out.
- IOS XE: `no bgp default ipv4-unicast`, then `activate` per family.
- `network` needs an exact RIB match — anchor aggregates with Null0.
- Redistributed routes are origin incomplete; filter every redistribution.
