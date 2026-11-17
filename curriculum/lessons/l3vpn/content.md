## The MPLS L3VPN model

An MPLS Layer 3 VPN (RFC 4364) lets one provider network carry many
customers' private IP networks, each isolated from the others, with
overlapping addresses allowed. Three roles:

| Role | Knows about |
|---|---|
| **CE** (customer edge) | Only its own site; talks plain IP (static or a routing protocol) to the PE |
| **PE** (provider edge) | One **VRF** per customer it serves, plus the core |
| **P** (provider core) | Only the core: IGP + labels. No customer routes, no VRFs |

The pieces:

- **VRF** (virtual routing and forwarding) — a separate routing table, CEF
  table and set of interfaces on the PE, per customer.
- **Route distinguisher (RD)** — 8 bytes prepended to a customer prefix to
  make a **VPNv4** prefix (`65000:100:10.1.1.0/24`). Its only job is to make
  overlapping prefixes unique inside BGP. It says nothing about which VRFs
  get the route.
- **Route target (RT)** — a BGP extended community attached on **export**.
  A PE **imports** a VPNv4 route into every VRF whose import list contains one
  of its RTs. RTs build the topology: full mesh, hub and spoke, extranet.
- **MP-BGP VPNv4** (AFI 1 / SAFI 128) carries VPNv4 routes, their RTs and a
  **VPN label** between PEs — normally through route reflectors.

## Forwarding: two labels

When A1 sends to `10.1.2.10`:

1. R3 looks it up in VRF **CUST-A**: a BGP route with next hop **4.4.4.4**
   (R4's loopback) and VPN label *V* (allocated by R4 for that VRF / prefix).
2. R3 pushes **two labels**: *V* at the bottom, and on top the **LDP label**
   for 4.4.4.4 that R1 advertised.
3. R1 (P) switches on the top label only. It knows nothing about customer A.
   As the penultimate hop it **pops** the LDP label (PHP).
4. R4 receives a packet labelled *V*, which tells it which VRF to look in,
   and forwards to A2.

```
R3# show ip cef vrf CUST-A 10.1.2.0/24 detail
10.1.2.0/24, epoch 0, flags [rib defined all labels]
  recursive via 4.4.4.4 label 21                ← VPN label (from R4)
    nexthop 10.0.13.1 GigabitEthernet2 label 24002   ← LDP label to 4.4.4.4
```

This is why the core can be **BGP-free**: P routers never look past the top
label. It's also why an LSP problem breaks *every* VPN at once.

## Configuring a VRF

```
! IOS XE                                  ! IOS XR
vrf definition CUST-A                     vrf CUST-A
 rd 65000:100                              address-family ipv4 unicast
 address-family ipv4                        import route-target
  route-target export 65000:100              65000:100
  route-target import 65000:100             export route-target
!                                            65000:100
interface GigabitEthernet3                interface GigabitEthernet0/0/0/1
 vrf forwarding CUST-A                     vrf CUST-A
 ip address 10.1.1.1 255.255.255.0         ipv4 address 10.1.1.1 255.255.255.0
!                                         router bgp 65000
router bgp 65000                           address-family vpnv4 unicast
 address-family vpnv4                      vrf CUST-A
  neighbor 1.1.1.1 activate                 rd 65000:100
  neighbor 1.1.1.1 send-community extended  address-family ipv4 unicast
 address-family ipv4 vrf CUST-A              redistribute connected
  redistribute connected
```

Differences to remember:

- On IOS XE the **RD lives in the VRF definition**, on IOS XR **under BGP's
  `vrf`**.
- `vrf forwarding` on IOS XE removes the interface's IP address. IOS XR
  refuses to move an addressed interface into a VRF.
- IOS XE turns on IPv4 unicast for every neighbor unless you configure `no
  bgp default ipv4-unicast`.
- IOS XR doesn't need routing policies on iBGP sessions, but does on eBGP,
  including PE–CE eBGP in a VRF.

**RD design.** Using the same RD on every PE for a customer (as here) is
simple, but a route reflector then sees two PEs' paths to a multihomed site
as the *same* VPNv4 prefix and reflects only one of them. A **unique RD per
PE per VRF** (e.g. `3.3.3.3:100`) keeps both paths visible, which gives
faster failover and load sharing.

## PE–CE routing

This lab's customers are directly connected hosts (connected routes). Real
CEs are routers, and the PE runs a routing protocol with them *inside the
VRF*:

| PE–CE protocol | Notes |
|---|---|
| **Static** | Simplest; `ip route vrf CUST-A …` / `router static vrf CUST-A` |
| **eBGP** | The most common. The customer often uses the *same* AS at every site, so the PE uses `as-override` or the CE uses `allowas-in`. Use **Site of Origin** (SoO) to stop routing loops on multihomed sites |
| **OSPF** | Redistributed into BGP with its route type preserved (domain ID, *down bit*). A **sham link** makes a backdoor link between sites lose to the VPN |
| **IS-IS, EIGRP, RIP** | Possible, redistributed the same way |

## Route reflectors for VPNv4

PEs normally peer only with route reflectors. Two things are special about
VPNv4 reflection:

- **Route-target filtering.** A PE drops VPNv4 routes whose RTs match none of
  its VRFs: why keep routes it can't use? A route reflector has no VRFs but
  must keep everything. IOS XR does this automatically once it has
  route-reflector clients; IOS XE needs `no bgp default route-target filter`.
- **RT constraint** (RFC 4684, `address-family rtfilter`): PEs tell the RR
  which RTs they import, and the RR sends each PE only those routes. This
  matters in large networks.

## Shared services: extranets

Route targets don't have to match one-to-one. An **extranet** is simply a
VRF that imports another VPN's RT:

```
VRF SHARED  : export 65000:500       import 65000:500, 65000:100
VRF CUST-A  : export 65000:100       import 65000:100, 65000:500
VRF CUST-B  : export 65000:200       import 65000:200
```

- Customer A and the shared service see each other. Customer B sees neither.
- **Imported routes are not re-exported**: SHARED's export RT only goes on
  SHARED's own routes, so A's routes don't leak to other importers of
  65000:500 through it.
- **Overlapping addresses don't mix.** If A and B both used SHARED, SHARED
  would hold two `10.1.1.0/24`s and keep one. Shared services need unique
  customer addressing (or NAT) on the prefixes that share.
- To share only *some* routes, use import/export **maps** (XE `import map`,
  XR `import route-policy` / `export route-policy`) to match prefixes and
  set or filter RTs.

A **hub-and-spoke** VPN uses the same mechanism. Spokes export *spoke* and
import *hub*; the hub site imports *spoke* and exports *hub*, so spoke-to-spoke
traffic goes through the hub.

## Internet access for VPN customers

Common designs:

| Design | How |
|---|---|
| **Internet VRF** (this lab) | The Internet routes (or just a default) live in a VRF; customers who buy access import its RT, and it imports theirs for the return path. Usually combined with NAT at the Internet edge for private customer addresses. A redistributed static default needs `default-information originate` (BGP skips 0.0.0.0/0 otherwise) |
| **Route leaking to the global table** | A static default in the VRF pointing into the global table (`ip route vrf CUST-B 0.0.0.0 0.0.0.0 <next hop> global`), plus static routes or NAT for the return path |
| **VRF-aware NAT** | `ip nat inside source … vrf CUST-B overload` at the PE or a central gateway, so overlapping private addresses share public ones |
| **Separate interface / sub-interface** | The CE gets a second link (or VLAN) in the global table for Internet traffic |

## Verification cheat sheet

| What | IOS XE | IOS XR |
|---|---|---|
| VPNv4 sessions | `show bgp vpnv4 unicast all summary` | `show bgp vpnv4 unicast summary` |
| VPNv4 table / one RD | `show bgp vpnv4 unicast all` / `… rd 65000:100` | `show bgp vpnv4 unicast` / `… rd 65000:100` |
| A VRF's BGP table | `show bgp vpnv4 unicast vrf CUST-A` | `show bgp vpnv4 unicast vrf CUST-A` |
| VRF routing table | `show ip route vrf CUST-A` | `show route vrf CUST-A` |
| Labels for a VRF prefix | `show ip cef vrf CUST-A <p> detail`, `show bgp vpnv4 unicast all labels` | `show cef vrf CUST-A <p>`, `show bgp vpnv4 unicast labels` |
| Ping / trace in a VRF | `ping vrf CUST-A <ip> source <if>` | `ping vrf CUST-A <ip>` |
| VRF details | `show vrf detail CUST-A` | `show vrf CUST-A detail` |
