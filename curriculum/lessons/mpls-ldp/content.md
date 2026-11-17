## Why MPLS in a service provider core

A service provider core forwards traffic for customers' and the Internet's
prefixes — hundreds of thousands of routes. **MPLS** lets the core forward on
a short **label** instead of the IP destination:

- **A BGP-free core.** Only the edge routers (PEs) run BGP and know customer
  and Internet routes. The P routers in the middle only need the IGP and
  labels to reach the PEs' loopbacks — smaller tables, faster convergence,
  and nothing to break when the Internet table grows.
- **Services.** The same label switched paths (LSPs) carry L3VPNs, L2VPNs
  (pseudowires, VPLS), and 6PE — each service adds an inner label the core
  never looks at.
- **Traffic engineering and fast reroute** (RSVP-TE, segment routing) steer
  LSPs explicitly.

Vocabulary:

| Term | Meaning |
|---|---|
| **LSR** | Label switch router — any MPLS router; **P** = core LSR, **PE** = edge |
| **FEC** | Forwarding equivalence class — the set of packets forwarded the same way (here: "towards 4.4.4.4/32") |
| **LIB** | Label information base — every binding learned from every neighbor |
| **LFIB** | Label forwarding table — the binding actually used (from the IGP's best next hop) |
| **Push / swap / pop** | Impose a label at the ingress PE, replace it in the core, remove it |
| **PHP** | Penultimate hop popping — the last P router pops the label (the egress advertised **implicit null**, label 3) so the egress PE does one lookup |

## How LDP works

LDP (RFC 5036) distributes labels for the prefixes the IGP already knows —
it follows the IGP, it doesn't compute paths.

1. **Discovery.** Link hellos (UDP 646 to 224.0.0.2) on every LDP interface
   find directly connected LDP neighbors. Each hello carries the router's
   **LDP ID** (`router-id:label-space`, e.g. `1.1.1.1:0`) and its **transport
   address** (by default the LDP router ID).
2. **Session.** The router with the higher transport address opens a TCP
   session to port 646 of the other's transport address — so that address
   must be **reachable**: always use the loopback, and advertise it in the
   IGP. (On IOS XE, `mpls ldp router-id Loopback0 force` makes the change
   immediate.)
3. **Label distribution.** By default every router allocates a local label for
   every IGP prefix (*independent control*) and advertises all its bindings
   to all neighbors (*downstream unsolicited*), and keeps every binding it
   receives (*liberal retention*). The LFIB uses the binding from the IGP's
   next hop.

```
! IOS XR                                  ! IOS XE
mpls ldp                                  mpls label protocol ldp
 router-id 1.1.1.1                        mpls ldp router-id Loopback0 force
 interface GigabitEthernet0/0/0/0         interface GigabitEthernet2
 interface GigabitEthernet0/0/0/1          mpls ip
```

Verify: XR `show mpls ldp neighbor brief`, `show mpls ldp discovery`, `show
mpls ldp bindings`, `show mpls forwarding`; XE `show mpls ldp neighbor`,
`show mpls ldp discovery`, `show mpls ldp bindings`, `show mpls
forwarding-table`, `show ip cef 4.4.4.4 detail` (the imposed label).

## MPLS OAM: LSP ping and traceroute

A normal IP ping can succeed over a broken LSP: a router without a label
simply forwards the packet as IP. **MPLS echo requests** (RFC 8029) are sent
*in the LSP*, labelled for a specific FEC, with the IP destination set to
127.0.0.x so they can't be IP-forwarded by mistake. Each LSR checks it has a
binding for the FEC (*target FEC stack* validation) and the egress answers.

```
R3# ping mpls ipv4 4.4.4.4/32
R3# traceroute mpls ipv4 4.4.4.4/32
  0 10.0.13.2 MRU 1500 [Labels: 24002 Exp: 0]
L 1 10.0.13.1 MRU 1500 [Labels: 24002 Exp: 0] 3 ms
L 2 10.0.12.2 MRU 1500 [Labels: implicit-null Exp: 0] 2 ms
! 3 10.0.24.2 3 ms
```

Return codes: `!` success (egress), `L` labelled output, `B` unlabelled
output (an LSP that breaks there), `F` no FEC mapping, `D` downstream map
mismatch, `.` timeout. IOS XR replies only with **`mpls oam`** configured.

## LDP–IGP synchronization

The IGP converges independently of LDP. When a link comes up — or a router
reboots — IS-IS may start using the link before the LDP session over it has
exchanged labels. Packets then leave the previous hop **unlabelled**; in a
BGP-free core the next P router doesn't know the customer destination and
**drops** them.

With **LDP–IGP sync** the IGP advertises the link with the **maximum metric**
(IS-IS 2²⁴−2 = 16777214, OSPF 65535) until LDP is operational on it, so
traffic only moves onto the link once labels are in place. The same happens
when LDP fails on a link that stays up.

```
! IOS XR (per interface)                  ! IOS XE (per process)
router isis CORE                          router isis CORE
 interface GigabitEthernet0/0/0/0          mpls ldp sync
  address-family ipv4 unicast
   mpls ldp sync
mpls ldp
 igp sync delay on-session-up 5   ! optional: wait for labels after the session is up
```

Verify: XR `show isis interface X` (*LDPv4 Sync Status: Achieved*), `show
mpls ldp igp sync`; XE `show mpls ldp igp sync`, `show isis database detail`
(the max metric while unsynchronised).

## LDP session protection

Link hellos only exist while the link is up. Lose the link and the session
goes, and with it every binding the neighbor sent — even though the neighbor
is still reachable over another path. When the link comes back, labels must
be re-exchanged before LDP–IGP sync lets traffic return.

**Session protection** adds **targeted hellos** (unicast to the neighbor's
transport address) for every directly connected neighbor. When the link
fails, the session stays up on the targeted adjacency and the bindings are
kept (*Protecting* state, for a configurable duration or forever); when the
link returns, forwarding resumes at once.

```
! IOS XR                                  ! IOS XE
mpls ldp                                  mpls ldp session protection
 session protection                       ! optional: for <acl> duration <s>
```

Verify: XR `show mpls ldp neighbor X detail` (*Session Protection: Enabled,
state: Ready / Protecting*, discovery sources); XE `show mpls ldp neighbor
detail`.

## LDP security

**Authentication.** An LDP session is a TCP connection: protect it like BGP,
with a **TCP MD5 signature** (or TCP-AO where supported). Both ends need the
same key; a mismatch keeps the session down. The key only applies to a new
TCP connection: IOS XE marks it *stale* on an established session until the
session is cleared.

```
! IOS XR                                  ! IOS XE
mpls ldp                                  mpls ldp neighbor 1.1.1.1 password <pw>
 neighbor 2.2.2.2:0 password clear <pw>   mpls ldp password required   ! refuse unsigned sessions
```

**Label allocation filtering.** Only allocate (and therefore advertise)
labels for the prefixes services need — normally the **PE loopbacks** — instead
of every IGP prefix:

```
! IOS XR                                  ! IOS XE
mpls ldp                                  mpls ldp label
 address-family ipv4                       allocate global host-routes
  label                                   ! or: allocate global prefix-list PL-LOOPBACKS
   local
    allocate for host-routes
    ! or: allocate for <ACL>
```

Related controls: **label advertisement filtering** (`label local advertise
to <peer> for <acl>` / `mpls ldp advertise-labels for <acl> to <acl>`) limits
what each neighbor receives; **accept filtering** (`label remote accept`)
limits what you keep.

## Verification cheat sheet

| What | IOS XR | IOS XE |
|---|---|---|
| Neighbors | `show mpls ldp neighbor brief` | `show mpls ldp neighbor` |
| Discovery | `show mpls ldp discovery` | `show mpls ldp discovery` |
| Bindings (LIB) | `show mpls ldp bindings` | `show mpls ldp bindings` |
| LFIB | `show mpls forwarding` | `show mpls forwarding-table` |
| Imposed label | `show cef 4.4.4.4` | `show ip cef 4.4.4.4 detail` |
| LSP ping / trace | `ping mpls ipv4 4.4.4.4/32` (needs `mpls oam`) | `ping mpls ipv4 4.4.4.4/32` |
| IGP sync | `show isis interface X`, `show mpls ldp igp sync` | `show mpls ldp igp sync` |
| Session protection | `show mpls ldp neighbor X detail` | `show mpls ldp neighbor X detail` |
