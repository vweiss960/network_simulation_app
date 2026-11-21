## Carrier Ethernet services (MEF)

The **MEF** (Metro Ethernet Forum) defines Ethernet services in terms the
customer buys, independent of how the provider builds them:

| Term | Meaning |
|---|---|
| **UNI** | User–network interface: the port where a customer hands traffic to the provider |
| **ENNI** | External NNI: the interface between two operators |
| **EVC** | Ethernet virtual connection: the association of UNIs that can exchange frames |
| **OVC** | Operator virtual connection: one operator's part of an EVC that crosses an ENNI |

The service types, each **port-based** (*private*: the whole UNI) or
**VLAN-based** (*virtual private*: some VLANs, several services per UNI):

| Service | Topology | Port-based | VLAN-based | Typically built with |
|---|---|---|---|---|
| **E-Line** | Point-to-point | EPL | EVPL | VPWS pseudowire, EVPN-VPWS |
| **E-LAN** | Multipoint-to-multipoint | EP-LAN | EVP-LAN | VPLS, EVPN |
| **E-Tree** | Rooted multipoint: leaves talk only to roots | EP-Tree | EVP-Tree | VPLS / EVPN with leaf split-horizon |
| **E-Access** | A UNI at one end, an **ENNI** at the other: a wholesale access circuit to another operator | Access EPL | Access EVPL | A pseudowire to the ENNI, S-VLAN per OVC |

**E-Tree** is a multipoint service where *leaf* sites can only reach *root*
sites, never each other. Examples are a hub data centre, or an ISP aggregating
subscribers who mustn't see each other. In VPLS or EVPN, leaf attachment
circuits and pseudowires get an extra split-horizon group, so traffic from a
leaf is never forwarded to another leaf. On IOS XR, `etree leaf` marks an
attachment circuit (or a whole bridge domain) as a leaf; everything else is a
root. EVPN E-Tree (RFC 8317) also advertises a leaf label so remote PEs filter
leaf-to-leaf traffic. In this lab R2 (XRd) shows the roles; no image here
forwards E-Tree traffic.

## Building blocks on the PE

**IOS XE: Ethernet service instances (EVC / EFP model).** One physical UNI
carries many services. Each service instance matches frames by their tags
and attaches them to a service:

```
interface GigabitEthernet3
 service instance 100 ethernet EL-A          ! EL-A = optional EVC name (for CFM)
  encapsulation dot1q 100                     ! match: dot1q 100 | untagged | default | dot1q 10 second-dot1q 20 | dot1ad 300 dot1q 10
  rewrite ingress tag pop 1 symmetric         ! tag manipulation
!
l2vpn xconnect context EL-A                   ! VPWS (point to point)
 member GigabitEthernet3 service-instance 100
 member 4.4.4.4 100 encapsulation mpls        ! remote PE, VC ID
!
bridge-domain 50                              ! multipoint (VPLS)
 member GigabitEthernet4 service-instance 50
 member vfi VPLS-B
```

**IOS XR: l2transport sub-interfaces and the l2vpn tree.**

In this lab, **R2** is an IOS XR PE running on **XRd Control Plane**, which
has a full L2VPN control plane but no Layer 2 data plane:

| | XRd Control Plane (R2) |
|---|---|
| `l2transport` interfaces / sub-interfaces, rewrites | Configured and up |
| Pseudowire signalling (targeted LDP, labels, VC type / MTU negotiation) | Works: xconnects and VPLS PWs come up with the IOS XE PEs |
| Bridge domain, VFI, xconnect state | Up |
| Label-switching other PEs' pseudowires (as a P router, like R1) | Works |
| Forwarding customer frames (AC ↔ PW) | **No**: AC and pseudowire counters stay at 0 |
| MAC learning | Local host MACs may appear (learned on the RP), but none cross a pseudowire: other PEs never learn them |
| E-Tree roles, PBB edge / core bridge domains | Configured and up (no traffic) |
| CFM / Y.1731, 802.3ah, G.8032 | Not in the CLI |

So R2's tasks check signalling, and traffic is proven between the IOS XE PEs.
On an ASR 9000, NCS or XRv 9000 the same configuration forwards:

```
interface GigabitEthernet0/0/0/3.100 l2transport
 encapsulation dot1q 100
 rewrite ingress tag pop 1 symmetric
!
l2vpn
 xconnect group CUST-A
  p2p EL-A
   interface GigabitEthernet0/0/0/3.100
   neighbor ipv4 4.4.4.4 pw-id 100
 bridge group CUST-B
  bridge-domain B
   interface GigabitEthernet0/0/0/4.50
   vfi VPLS-B
    neighbor 3.3.3.3 pw-id 50
    neighbor 5.5.5.5 pw-id 50
```

## VLAN tag manipulation

Rewrites change the tags of frames that match a service instance. With
`symmetric`, the router applies the inverse operation on egress:

| Rewrite | Ingress | Egress (symmetric) | Use |
|---|---|---|---|
| `pop 1` | Remove the outer tag | Push the instance's tag back | VLAN translation via the core (this lab's E-Line); stripping an S-tag |
| `pop 2` | Remove both tags | Push both back | Q-in-Q in, untagged across the core |
| `push dot1q 300` | Add an outer tag | Remove it | Add an S-tag at a UNI (Q-in-Q) |
| `translate 1-to-1 dot1q 200` | Replace the tag | Replace it back | VLAN translation on one PE |

The pseudowire itself carries the frame as it is after the ingress rewrite
(VC type 5, *Ethernet* or "raw"). Both ends must agree on what that is:
here both pop, so the frame crosses untagged.

## 802.1ad, 802.1ah and G.8032

**802.1Q** adds one VLAN tag (ethertype 0x8100, 12-bit VLAN ID).

**802.1ad (provider bridges, "Q-in-Q")** adds an outer **S-tag** (service VLAN,
ethertype **0x88A8**) in front of the customer's **C-tag**. The provider
switches on the S-tag only, so each customer keeps its full VLAN space. A
provider has 4094 S-VLANs: one per service.

**802.1ah (provider backbone bridges, PBB, "MAC-in-MAC")** goes one step
further. The backbone edge wraps the whole customer frame in a new Ethernet
header with **backbone MAC addresses** (B-DA / B-SA), a **B-VLAN**, and a
24-bit **I-SID** (service instance ID): 16 million services. Core switches
learn only backbone MACs, never customer MACs. PBB-EVPN combines it with
EVPN on IOS XR (ASR 9000). On XR a **PBB edge** bridge domain (customer ACs,
`pbb edge i-sid … core-bridge …`) feeds a **PBB core** bridge domain (`pbb
core`). R2 (XRd) builds both here, configuration and state only.

**ITU-T G.8032 (Ethernet ring protection switching, ERPS)** gives
sub-50 ms protection on Ethernet rings without spanning tree:

- One link on each ring is the **RPL** (ring protection link). Its **owner**
  keeps it blocked in normal operation, so the ring has no loop.
- When a link fails, the adjacent nodes block it and send **R-APS** (signal
  fail) messages. The RPL owner unblocks the RPL and every node flushes its MAC
  table.
- When the link recovers, the **wait-to-restore** timer runs, then the RPL is
  blocked again (revertive mode).
- Sub-rings and multiple ERP instances, each with its own RPL, load-share
  VLANs across the ring.

It's configured on ASR 9000 / NCS (`l2vpn ethernet ring g8032`) and access
switches such as the ASR 920 / ME (`ethernet ring g8032`). Neither of this
lab's images has it.

## VPLS

A **VPLS** instance makes the provider network look like one switch:

- Each PE has a bridge domain that **learns MAC addresses** from its
  attachment circuits and from the pseudowires.
- The PEs are joined by a **full mesh of pseudowires** (the VFI), signalled by
  targeted LDP (RFC 4762), or discovered with BGP (RFC 4761 / BGP-AD).
- **Split horizon**: frames received on a pseudowire are never forwarded to
  another pseudowire, which prevents loops without spanning tree.
- Unknown unicast, broadcast and multicast are flooded to every pseudowire.
  This scales poorly, so very large VPLS designs use **H-VPLS**: a spoke
  pseudowire from each access PE to a hub PE.

**EVPN** (RFC 7432) is the modern replacement. It learns MAC addresses in BGP
instead of by flooding, and supports all-active multihoming, ARP suppression
and E-Tree.

## Ethernet OAM

Three layers:

| Standard | Scope | What it does |
|---|---|---|
| **802.3ah (EFM / link OAM)** | One physical link | Discovery, link monitoring (error thresholds), remote failure indication, **remote loopback** of the link |
| **802.1ag CFM** | An Ethernet *service*, end to end | **CCM** continuity checks, **loopback** (Ethernet ping), **linktrace** (Ethernet traceroute) |
| **ITU-T Y.1731** | Adds to CFM | **Performance monitoring**: frame delay and delay variation (DMM/DMR), frame loss (LMM/LMR, SLM), AIS, LCK |
| **E-LMI** | UNI | Tells the CE which EVCs exist on its UNI and their status |

CFM concepts:

- **Maintenance domains** have a **level** 0–7 so that customer, provider and
  operator OAM don't collide. Customers typically use 5–7, providers 3–4 and
  operators 0–2. A MEP passes higher-level CFM frames through transparently
  and drops lower-level ones.
- **MEP** (maintenance endpoint): at the edge of a service, it originates
  CCMs and answers loopback / linktrace. An **up MEP** faces the bridge or the
  service (the core); a **down MEP** faces the wire.
- **MIP** (maintenance intermediate point): inside the service, it answers
  loopback / linktrace but sends no CCMs.
- Each MEP has an **MPID** unique in its service, and learns remote MEPs from
  their CCMs. A missing CCM for 3.5 intervals is a **loss of continuity** fault.

```
! Down MEPs on a link (this lab: R4 Gi5 <-> R5 Gi5), a port service
ethernet cfm global
ethernet cfm domain OPERATOR level 2
 service R4-R5 port
  continuity-check
  continuity-check interval 1s
interface GigabitEthernet5
 ethernet cfm mep domain OPERATOR mpid 45 service R4-R5

! Up MEPs on an E-Line's UNI service instances (ASR 920 / ASR 900 IOS XE;
! not on the CSR 1000v, which has no up MEPs)
ethernet cfm domain PROVIDER level 4
 service EL-A evc EL-A
  continuity-check
interface GigabitEthernet0/0/3
 service instance 100 ethernet EL-A
  encapsulation dot1q 100
  rewrite ingress tag pop 1 symmetric
  cfm mep domain PROVIDER mpid 3100

! IOS XR (ASR 9000 / NCS)
ethernet cfm
 domain PROVIDER level 4
  service EL-A xconnect group CUST-A p2p EL-A
   continuity-check interval 1s
   mep crosscheck
    mep-id 4100
interface GigabitEthernet0/0/0/3.100 l2transport
 ethernet cfm
  mep domain PROVIDER service EL-A mep-id 3100

show ethernet cfm maintenance-points remote
ping ethernet mpid 54 domain OPERATOR service R4-R5
traceroute ethernet mpid 54 domain OPERATOR service R4-R5
```

802.3ah link OAM (`ethernet oam` on an interface; `ethernet oam remote-loopback
start interface …` puts the peer in loopback for a line test) runs between two
directly connected Ethernet devices, for example a PE and the customer's
demarcation device, or both ends of a wholesale circuit as in this lab.

## Verification cheat sheet

| What | IOS XE | IOS XR |
|---|---|---|
| Pseudowires | `show l2vpn atom vc`, `show mpls l2transport vc <id> detail` | `show l2vpn xconnect [detail]` |
| VPLS | `show l2vpn vfi`, `show bridge-domain 50` | `show l2vpn bridge-domain [detail]` |
| MAC table | `show bridge-domain 50 mac-address` | `show l2vpn forwarding bridge-domain mac-address location …` |
| One bridge domain | `show bridge-domain 50` | `show l2vpn bridge-domain bd-name B [detail]` |
| Service instances | `show ethernet service instance [detail]` | `show interfaces …l2transport`, `show l2vpn xconnect interface …` |
| CFM | `show ethernet cfm maintenance-points local / remote`, `ping ethernet …` | `show ethernet cfm peer meps`, `ping ethernet cfm …` |
| Link OAM | `show ethernet oam summary` | `show ethernet oam summary` |
