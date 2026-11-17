## Why split the IGP

A single IGP and LDP domain works well up to a few hundred routers. Large
service providers have **tens of thousands** of nodes: access routers in
every cell site and street cabinet, aggregation routers, the core. One flat
domain then hits limits:

- **Every router carries every loopback** and every link in its LSDB and LFIB.
  Small access routers have little memory and CPU.
- **Every change floods everywhere** and triggers SPF on every router: a flapping
  access link costs CPU in the core.
- **LDP** builds a label for every prefix on every router.

**Unified MPLS** (also called *Seamless MPLS*) splits the network into
independent IGP domains, usually by **area** or by separate **IGP
instances/protocols**: one per access or aggregation region, plus the core.
Inside each domain, IGP and LDP work as usual. Between domains, nothing is
redistributed. The PE loopbacks that services need cross the boundaries in
**BGP**, *with labels*.

## BGP labeled unicast (RFC 8277, formerly RFC 3107)

BGP-LU is IPv4 unicast BGP (AFI 1 / SAFI 4) where each route carries an MPLS
label, just as VPNv4 routes do. A router that advertises a labeled route
promises: *"send me packets for this prefix with this label and I'll get them
there."*

```
! IOS XE                                  ! IOS XR
router bgp 65000                          router bgp 65000
 no bgp default ipv4-unicast               address-family ipv4 unicast
 neighbor 1.1.1.1 remote-as 65000           network 5.5.5.5/32
 neighbor 1.1.1.1 update-source Loopback0   allocate-label all
 address-family ipv4                       neighbor 3.3.3.3
  network 4.4.4.4 mask 255.255.255.255      remote-as 65000
  neighbor 1.1.1.1 activate                 update-source Loopback0
  neighbor 1.1.1.1 send-label               address-family ipv4 labeled-unicast
```

- IOS XE: `send-label` on the neighbor in `address-family ipv4`.
- IOS XR: a separate `address-family ipv4 labeled-unicast` on the neighbor
  (sharing the IPv4 unicast table), and `allocate-label` (`all` or a route
  policy) so the router allocates local labels for BGP routes.

## Stitching the LSP: border routers and next-hop-self

The border routers (ABRs, or *area border routers* in the Unified MPLS
design) sit in two IGP domains and are **inline route reflectors** for the
PEs in their access domain. Their key job is **next-hop-self**: on every
labeled route they pass on, they set themselves as the next hop and
advertise **their own label**. So:

1. R5 advertises 5.5.5.5/32, label *implicit null* (it's the egress), to R3.
2. R3 allocates label *L3* for 5.5.5.5, sets next hop 3.3.3.3, and reflects it
   to R1.
3. R1 allocates *L1*, sets next hop 1.1.1.1, and reflects it to R4.
4. R4 sends to 5.5.5.5: push **L1** and the LDP label for 1.1.1.1 (implicit
   null here: they're neighbors).
5. R1 swaps L1 → **L3** and pushes the LDP label for 3.3.3.3. R2 switches
   on the LDP label and pops it.
6. R3 swaps L3 → implicit null (pop) and forwards over the East LDP domain
   to R5.

Every router only ever needs a route and an LDP label to the **next hop in
its own domain**. The core router R2 never learns the PE loopbacks.

Reflected routes and next-hop-self:

| | IOS XE | IOS XR |
|---|---|---|
| Change the next hop of reflected iBGP routes | `neighbor X next-hop-self all` (plain `next-hop-self` leaves reflected routes alone) | `next-hop-self` under the neighbor's address family, plus `ibgp policy out enforce-modifications` under `router bgp` |

## Services on top

To a service, the BGP-LU route to the remote PE's loopback is just a route
with a label. A VPNv4 route with next hop 5.5.5.5 **recursively resolves**
through it:

```
R4# show ip cef vrf CUST 10.5.1.0/24 detail
10.5.1.0/24, epoch 0, flags [rib defined all labels]
  recursive via 5.5.5.5 label 24002         ← VPN label (from R5)
    recursive via 1.1.1.1 label 24004       ← BGP-LU label (from R1) for 5.5.5.5
      nexthop 10.1.14.1 GigabitEthernet2    ← LDP to 1.1.1.1: implicit null

R4# traceroute vrf CUST 10.5.1.10 numeric
  1 10.1.14.1 [MPLS: Labels 24004/24002 Exp 0]          ← R1: BGP-LU / VPN
  2 10.0.12.2 [MPLS: Labels 24001/18/24002 Exp 0]       ← R2: LDP to 3.3.3.3 / R3's BGP-LU / VPN
  3 10.0.23.2 [MPLS: Labels 18/24002 Exp 0]             ← R3: its BGP-LU label / VPN
  4 10.2.35.2                                           ← R5
  5 10.5.1.10
```

In the core, a customer packet carries **three labels**. R2 only looks at the
top one.

In a full design the VPNv4 sessions also go through route reflectors, often
the same border routers (`next-hop unchanged` for VPNv4, so the VPN next hop
stays the remote PE). Some designs use **LDP downstream-on-demand** in the
access domain, so a small access node only requests labels for the few
loopbacks it needs.

## Variations

- **Areas instead of protocols.** The domains can be IS-IS levels or OSPF
  areas of one IGP, with ABR loopbacks leaked and PE loopbacks kept
  per-area. Separate instances (as here) give the strongest isolation.
- **Inter-AS MPLS** uses the same building block across AS boundaries: option
  C exchanges PE loopbacks between ASes with eBGP-LU.
- **BGP PIC** (prefix independent convergence) with redundant ABRs gives
  fast failover for the BGP-LU paths.
- **Segment routing** replaces LDP inside each domain (SR-MPLS), and BGP-LU
  or an SR-PCE stitches the domains.

## Verification cheat sheet

| What | IOS XE | IOS XR |
|---|---|---|
| BGP-LU sessions | `show bgp ipv4 unicast summary` | `show bgp ipv4 labeled-unicast summary` |
| Labels in BGP | `show bgp ipv4 unicast labels` | `show bgp ipv4 labeled-unicast labels` |
| One prefix | `show bgp ipv4 unicast 5.5.5.5/32` | `show bgp ipv4 unicast 5.5.5.5/32` |
| Imposed label stack | `show ip cef 5.5.5.5 detail` | `show cef 5.5.5.5` |
| Label swaps | `show mpls forwarding-table` | `show mpls forwarding` |
| End-to-end test | `ping 5.5.5.5 source Loopback0` | `ping 4.4.4.4 source Loopback0` |
