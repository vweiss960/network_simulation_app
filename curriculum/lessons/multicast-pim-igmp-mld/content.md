## Multicast addressing

| Range | Use |
|---|---|
| 224.0.0.0/24 | Link-local control (never routed): 224.0.0.1 all hosts, .2 all routers, .5/.6 OSPF, .13 all PIM routers, .22 IGMPv3 reports |
| 224.0.1.0/24 | Internetwork control (e.g. 224.0.1.39 / .40 Auto-RP) |
| 232.0.0.0/8 | **Source-specific multicast (SSM)** |
| 233.0.0.0/8 | GLOP (an AS number embedded in the group) |
| 239.0.0.0/8 | **Administratively scoped** (private, like RFC 1918) |

An IPv4 group maps to an Ethernet MAC **01:00:5e** + the low **23 bits** of
the group, so 32 groups share each MAC (239.1.1.1 and 224.129.1.1 both map to
01:00:5e:01:01:01).

IPv6 groups are **ff**`<flags><scope>`**::/8**: the scope nibble is 1
interface-local, 2 link-local (ff02::1 all nodes, ff02::d all PIM routers),
5 site-local, 8 organization-local, e global. **ff3x::/32** is the SSM range and
**ff7x::/12** carries an **embedded RP**. The MAC is **33:33** + the low 32
bits.

## Distribution trees and RPF

Multicast is forwarded *away from the source*. A router accepts a packet only
on the interface it would use to reach the **source** (or, on a shared tree,
the **RP**) in the unicast routing table: the **reverse path forwarding
(RPF) check**. It prevents loops and duplicates, and it's why multicast
follows the IGP: a unicast routing change moves the trees.

- **Source tree / shortest-path tree (S,G)**: rooted at the source's
  first-hop router, the shortest path to every receiver. More state, best
  paths.
- **Shared tree (\*,G)**: rooted at the **rendezvous point**; every source
  sends to the RP, which forwards down the tree. Less state, possibly longer
  paths.

RPF uses the unicast RIB by default. A separate multicast topology (IS-IS
multi-topology, BGP `ipv4 multicast` / MBGP, static `ip mroute`) lets
multicast follow different paths.

## PIM sparse mode

- **Neighbors**: PIM hellos to 224.0.0.13 every 30 s. The neighbor with the
  highest DR priority (then highest IP) is the LAN's **designated router**:
  the one that registers sources and joins for receivers.
- **Joins and prunes** go hop by hop to the RPF neighbor, refreshed every 60 s.
  (\*,G) joins go towards the RP, (S,G) joins towards the source.
- **Register**: the first-hop DR encapsulates the source's packets in
  unicast *register* messages to the RP. The RP decapsulates them down the
  shared tree, joins the source natively, and sends a *register-stop* once
  native traffic arrives.
- **SPT switchover**: a last-hop router that receives the first packet over
  the shared tree joins the source directly (`ip pim spt-threshold infinity`
  keeps groups on the shared tree), then prunes (S,G) off the RPT
  ("(S,G,rpt) prune").
- **Assert**: when two routers would forward onto the same LAN, they exchange
  asserts and the one with the better route to the source wins.

**PIM dense mode** (flood and prune, state in every router) is obsolete in
service provider networks.

## SSM

**Source-specific multicast** (RFC 4607) drops the RP altogether: the
receiver names the source in an IGMPv3 / MLDv2 INCLUDE report, and the
last-hop router sends an (S,G) join straight to it. It needs:

- IGMPv3 / MLDv2 on the receiver LAN (or **SSM mapping** for v1/v2 hosts: the
  router maps a group to sources from DNS or static configuration,
  `ip igmp ssm-map enable` + `ip igmp ssm-map static <acl> <source>` on IOS XE),
- the SSM range enabled on every router (default 232/8, ff3x::/32).

There's no register, no shared tree, no SPT switchover, no RP to protect, and
two sources using the same group are separate channels. It's the preferred
model for IPTV and one-to-many services, and the base of most mVPN designs.

## PIM-BIDIR

Bidirectional PIM (RFC 5015) builds **only shared trees**: traffic from any
source flows *up* to the RP and *down* to the receivers on the same (\*,G).
There's no (S,G) state and no register, so state doesn't grow with the number
of sources: ideal for many-to-many groups.

Each link elects a **designated forwarder (DF)**, the router with the best
metric to the RP address. Only the DF forwards traffic upstream from the link
towards the RP, which replaces the RPF check. The RP address only needs to be
routable: a **phantom RP** (an address on a subnet, not a router) is a
common redundancy design.

## RP discovery and redundancy

| Method | How |
|---|---|
| Static | `rp-address` on every router; simple, no failover |
| **Auto-RP** (Cisco) | Candidate RPs announce to 224.0.1.39; the **mapping agent** picks one per range and announces to 224.0.1.40. Needs `ip pim autorp listener` (or sparse-dense mode) to flood those two groups |
| **BSR** (standard, PIMv2) | Candidate RPs unicast to the elected **BSR**, which floods the RP set hop by hop; every router hashes groups onto RPs |
| Embedded RP (IPv6) | The RP address is inside the ff7x group address |
| **Anycast RP** | Several RPs share one address; receivers and sources use the nearest. RPs exchange active sources with **MSDP** (or PIM anycast RP, RFC 4610) |

## IGMP and MLD

| | IGMPv1 | IGMPv2 | IGMPv3 | MLDv1 | MLDv2 |
|---|---|---|---|---|---|
| Querier election | None (relies on PIM DR) | Lowest IP | Lowest IP | Lowest IP | Lowest IP |
| Leave | Time out | **Leave Group** + group-specific query | State-change report | Done message | State-change report |
| Source filtering (SSM) | No | No | **INCLUDE / EXCLUDE** | No | **Yes** |
| Report sent to | the group | the group | **224.0.0.22** | the group | **ff02::16** |

A host answers with the version of the queries it hears, so the querier's
version caps what the LAN can do. Switches use **IGMP / MLD snooping** to
forward groups only to the ports that asked for them.

| | IOS XR | IOS XE |
|---|---|---|
| Default version | IGMPv3 / MLDv2 | IGMPv2 / MLDv2 |
| Change | `router igmp` → `interface X` → `version 2` | `interface X` → `ip igmp version 3` |
| Static join (router forwards it) | `router igmp` → `interface X` → `static-group G` | `ip igmp static-group G` |
| Router joins itself (answers pings) | `router igmp` → `interface X` → `join-group G` | `ip igmp join-group G` |

## Multicast in a service provider network

Customer multicast crosses an MPLS core as **multicast VPN (mVPN)**: per-VRF
PIM on the PEs, carried over the core in **provider tunnels**: GRE with core
PIM (the original "Rosen" draft, profile 0), **mLDP** point-to-multipoint
LSPs (profiles 1, 12, 14…), RSVP-TE P2MP, or ingress replication. BGP
**MVPN routes** (auto-discovery, C-multicast joins) replace PE-to-PE PIM in
the newer profiles. This lab builds the native (global table) multicast those
designs start from.

## Verification cheat sheet

| What | IOS XE | IOS XR |
|---|---|---|
| PIM neighbors | `show ip pim neighbor` | `show pim neighbor` |
| PIM interfaces / DR | `show ip pim interface` | `show pim interface` |
| RP for a group | `show ip pim rp mapping [G]` | `show pim group-map [G]` |
| BSR | `show ip pim bsr-router` | `show pim bsr election`, `show pim bsr rp-cache` |
| Multicast routing table | `show ip mroute [G]` | `show pim topology [G]` (PIM), `show mrib route [G]` (forwarding) |
| Packet counts | `show ip mroute count` | `show mfib route [G] statistics` |
| RPF check | `show ip rpf 10.1.3.10` | `show pim rpf 10.1.3.10` |
| BIDIR DF | `show ip pim interface df` | `show pim df winner` |
| IGMP | `show ip igmp interface`, `show ip igmp groups [detail]` | `show igmp interface`, `show igmp groups [detail]` |
| IPv6 | `show ipv6 pim neighbor`, `show ipv6 mld groups`, `show ipv6 mroute` | `show pim ipv6 neighbor`, `show mld groups`, `show mrib ipv6 route` |
| Test from a router | `ping 239.1.1.1 repeat 3` (answered by joined routers) | `ping 239.1.1.1 count 3` |
