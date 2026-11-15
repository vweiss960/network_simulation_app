## The BGP best-path algorithm

When BGP has more than one path to a prefix it compares them **pairwise, in
a fixed order**, and stops at the first step that differs. A path is only a
candidate if its next hop is reachable and it isn't looped (AS path,
ORIGINATOR_ID, CLUSTER_LIST).

| # | Prefer | Notes |
|---|---|---|
| 1 | Highest **weight** | Cisco only, local to the router, never advertised (default 0; 32768 for locally originated routes) |
| 2 | Highest **local preference** | Shared across the AS by iBGP (default 100) |
| 3 | **Locally originated** | `network`, `aggregate-address`, redistribution |
| 4 | Shortest **AS path** | An AS_SET counts as one; confederation segments don't count |
| 5 | Lowest **origin** | IGP (`i`) < EGP (`e`) < incomplete (`?`) |
| 6 | Lowest **MED** | Only between paths from the **same neighbor AS** (unless `always-compare-med`) |
| 7 | **eBGP** over iBGP | |
| 8 | Lowest **IGP metric** to the next hop | "Hot potato": leave the AS at the nearest exit |
| 9 | (multipath) | Paths equal so far can be installed together with `maximum-paths` |
| 10 | **Oldest** eBGP path | Stability: avoids flapping between equal paths |
| 11 | Lowest **router ID** | Of the advertising router (the ORIGINATOR_ID for reflected routes) |
| 12 | Shortest **CLUSTER_LIST** | Route-reflected paths |
| 13 | Lowest **neighbor address** | |

A useful mnemonic for the first steps: **W**e **L**ove **O**ranges **AS**
**O**ranges **M**ean **P**ure **R**efreshment — Weight, Local preference,
Originated, AS path, Origin, MED, Paths (eBGP over iBGP), Router ID (with the
IGP metric in between).

IOS XR explains its decision: `show bgp ipv4 unicast <prefix> bestpath-compare`
prints, for every losing path, the step that eliminated it.

## Attributes you'll use

| Attribute | Type | Scope | Typical use |
|---|---|---|---|
| **Weight** | Cisco-specific | the router itself | Override the decision on one router (avoid in an SP core: invisible to the rest of the AS) |
| **Local preference** | well-known discretionary | the whole AS (iBGP) | **Outbound** traffic: which exit the AS uses |
| **AS path** | well-known mandatory | everywhere | Loop prevention; **inbound** traffic via prepending |
| **Origin** | well-known mandatory | everywhere | Rarely changed on purpose |
| **MED** | optional non-transitive | neighbor AS only | Tell a neighbor AS which of **your** links it should use, when it connects to you twice |
| **Community** | optional transitive | everywhere (if sent) | **Tags** that policies match on: customer / peer / upstream, region, "don't export"… |

Two well-known communities matter in every network: **no-export**
(don't advertise outside the AS / confederation) and **no-advertise** (don't
advertise to any peer).

**Outbound vs inbound traffic.** You fully control how traffic **leaves**
your AS (local preference, weight). Traffic **entering** your AS follows the
*other* networks' decisions — you can only influence them with what you
send: AS-path prepending, MED (to one neighbor AS), more-specific prefixes,
or communities that the neighbor has published as "set my local preference".

## Communities: tag on ingress, filter on egress

The pattern every provider uses:

1. **On ingress**, tag each route with where it came from — e.g.
   `65000:100` customer, `65000:200` upstream A, `65000:300` upstream B.
2. **On egress**, decide by tag: customers get everything; upstreams and
   peers get only **customer routes and your own blocks**. Anything else
   would make you a **transit** for your upstreams.

The policy at the edge never has to know individual prefixes — a new
customer's routes are exported correctly the moment they're tagged.

Communities only cross the AS if every router sends them: IOS XR sends them
to iBGP peers by default (`send-community-ebgp` for eBGP); **IOS XE sends
none** until you configure `neighbor … send-community`.

## IOS XR routing policy language (RPL)

RPL is a small programming language. Policies are built from **sets** and
**if / then / else** logic, and can call each other.

```
community-set CUSTOMER
  65000:100
end-set
!
prefix-set OWN-BLOCKS            ! IPv4 and IPv6 entries can mix
  100.64.0.0/16,
  2001:db8:6500::/48
end-set
!
as-path-set ORIGINATED-BY-B
  ios-regex '^65300$'
end-set
!
route-policy TO-UPSTREAM
  if community matches-any CUSTOMER then
    pass
  elseif destination in OWN-BLOCKS then
    pass
  else
    drop
  endif
end-policy
!
route-policy TO-UPSTREAM-B
  apply TO-UPSTREAM              ! reuse; a drop inside stays a drop
  prepend as-path 65000 2
end-policy
```

- A route that reaches the end without `pass` (or a `set`) is **dropped**.
- Sets can also be written inline: `if destination in (10.1.14.0/30) then`.
- Policy changes take effect at **commit** — XR soft-refreshes the
  affected sessions itself.
- `show rpl route-policy TO-UPSTREAM` · `show bgp ipv4 unicast policy route-policy TO-UPSTREAM`
  (what the policy would do to the current table).

## IOS XE route maps

Route maps are ordered lists of **permit / deny** entries, each with
`match` and `set` clauses. The first entry that matches decides; a route
that matches nothing is **denied** (implicit deny at the end).

```
ip community-list standard CUSTOMER permit 65000:100
ip prefix-list OWN-BLOCKS seq 5 permit 100.64.0.0/16
!
route-map TO-UPSTREAM permit 10
 match community CUSTOMER          ! entries = OR
!
route-map TO-UPSTREAM permit 20
 match ip address prefix-list OWN-BLOCKS
!
route-map FROM-UPSTREAM-A permit 10   ! no match = everything
 set local-preference 200
 set community 65000:200 additive
```

- Several `match` lines **in one entry** must all match (AND); separate
  entries are alternatives (OR).
- IPv6 prefixes need their own `ipv6 prefix-list` and usually a separate
  route map.
- XE applies a changed **inbound** policy only to new updates: run
  `clear ip bgp <neighbor> soft in` (route refresh, the session stays up).
  The same goes for `soft out` after changing an outbound policy.
- `ip bgp-community new-format` displays communities as `AS:NN`.

## Policy in the IGP

The same policy tools control **IGP redistribution** — into IS-IS or OSPF:

```
! IOS XR
router isis CORE
 address-family ipv4 unicast
  redistribute connected level-2 route-policy PE-CE-LINKS

! IOS XE
router isis CORE
 redistribute connected level-2 route-map PE-CE-LINKS
```

`set tag` marks redistributed routes (IS-IS needs wide metrics to carry
tags), so later policies — or an operator — can recognise them. Always
redistribute through a policy: an unfiltered `redistribute connected` also
pulls in management and any other connected subnet.

## Verification commands

| What | IOS XR | IOS XE |
|---|---|---|
| Why a path won / lost | `show bgp ipv4 unicast <prefix> bestpath-compare` | `show bgp ipv4 unicast <prefix>` |
| What you advertise to a neighbor | `show bgp neighbor X advertised-routes` | `show bgp ipv4 unicast neighbors X advertised-routes` |
| Routes with a community | `show bgp ipv4 unicast community 65000:100` | `show bgp ipv4 unicast community 65000:100` |
| AS-path regex test | `show bgp ipv4 unicast regexp ^65300$` | `show bgp ipv4 unicast regexp ^65300$` |
| The policy itself | `show rpl route-policy NAME` | `show route-map NAME` (with match counters) |

## Wrap-up and exam tips

- Know the order: weight, local pref, locally originated, AS path, origin,
  MED, eBGP over iBGP, IGP metric, oldest, router ID, cluster list, neighbor.
- Outbound traffic: local preference (AS-wide). Inbound: prepending, MED,
  more-specifics, communities — the neighbor decides.
- MED is only compared between paths from the same neighbor AS.
- Tag on ingress with communities, filter on egress — never be transit for
  your upstreams.
- XR: RPL sets + if/then/else, unmatched = drop, `apply` to reuse.
  XE: route maps, first match wins, implicit deny, soft-clear after changes.
- XE needs `send-community`; XR sends communities to iBGP by default.
