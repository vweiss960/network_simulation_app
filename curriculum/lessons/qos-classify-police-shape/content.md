## QoS models

| Model | How | Where it's used |
|---|---|---|
| Best effort | No differentiation: one FIFO queue | The default |
| **IntServ** | Per-flow reservations signalled end to end with **RSVP** | Doesn't scale to the core; RSVP survives as RSVP-TE for MPLS tunnels |
| **DiffServ** | Traffic is classified and **marked** once at the edge; every hop applies a *per-hop behavior* (PHB) to the marking | Service provider and enterprise networks |

DiffServ in an SP network: the **ingress PE** classifies, marks and polices
at the **trust boundary** (the UNI), the **core** queues on the marking only
(DSCP or MPLS EXP), the **egress PE** shapes and queues towards the customer.

## Markings and per-hop behaviors

The IPv4 ToS / IPv6 Traffic Class byte carries a 6-bit **DSCP** and 2 bits of
**ECN**.

| PHB | DSCP | Decimal | ToS byte | Use |
|---|---|---|---|---|
| Default (BE) | 000000 | 0 | 0x00 | Best effort |
| **EF** (expedited forwarding) | 101110 | 46 | 0xb8 | Voice: low loss, latency, jitter |
| **AFxy** (assured forwarding) | xxxyy0 | 8x + 2y | | Class x = 1–4 (queue), y = 1–3 **drop precedence** |
| AF11 / AF12 / AF13 | | 10 / 12 / 14 | 0x28 / 0x30 / 0x38 | Bulk data: within contract / marked down / further marked down |
| AF41 | | 34 | 0x88 | Interactive video |
| **CSx** (class selector) | xxx000 | 8x | | Backward compatible with IP precedence; CS6 = routing protocols |

In **MPLS** the label's 3-bit **EXP** (renamed *Traffic Class*) carries the
class across the core: 8 values instead of 64, so DSCPs are mapped onto them
(EF → 5, AF1x → 1, CS6 → 6…). On a layer 2 trunk the 802.1Q **PCP** (CoS)
field plays the same role.

## MPLS DiffServ tunneling modes

What happens to the customer's own DSCP across the provider (RFC 3270):

| Mode | Label imposition | In the core | At disposition |
|---|---|---|---|
| **Uniform** | EXP copied from DSCP | Re-marking EXP changes the IP DSCP too (copied back) | Egress queues on the (re-marked) DSCP |
| **Short pipe** | EXP set by the provider | Provider EXP only | Customer DSCP untouched; egress PE queues on the **customer's DSCP** |
| **Pipe** | EXP set by the provider | Provider EXP only | Customer DSCP untouched; egress PE queues on the **provider's EXP** (kept in a `qos-group`) |

In this lab R3 re-marks the customer DSCP at the UNI, then sets EXP
explicitly with `set mpls experimental imposition`.

## Classification and marking

- **Classify** with class-maps: `match access-group`, `match dscp`,
  `match precedence`, `match mpls experimental topmost`, `match protocol`
  (NBAR, IOS XE), `match cos`, `match qos-group`. `match-any` = OR,
  `match-all` = AND.
- **Mark** with `set dscp`, `set precedence`, `set mpls experimental
  imposition | topmost`, `set cos`, `set qos-group` (an internal label that
  never leaves the router: used to carry a class from input to output policy).
- Mark as close to the source as you can trust it; re-mark at every trust
  boundary.

## Policing and shaping

Both meter traffic with a **token bucket**: tokens are added at the
committed rate (**CIR**), a burst (**Bc**, bytes) can be saved up, and a
packet needs tokens to conform.

| | Policer | Shaper |
|---|---|---|
| Excess traffic | Dropped or re-marked **immediately** | **Buffered** and sent later |
| Delay / jitter | None added | Adds delay |
| Direction | Input or output | Output |
| Typical use | Enforce a contract at the ingress | Match a slower downstream rate (e.g. a 400 kb/s service on a 1 Gb/s port), so the provider's policer doesn't drop it |

Policer types:

| Type | Buckets | Colors |
|---|---|---|
| Single-rate two-color | CIR, Bc | conform / exceed |
| Single-rate three-color (srTCM, RFC 2697) | CIR, Bc, Be | conform / exceed / violate |
| **Two-rate three-color** (trTCM, RFC 2698) | CIR + **PIR** | conform / exceed / violate |

Each color gets an action: `transmit`, `drop`, `set-dscp-transmit`,
`set-mpls-exp-imposition-transmit`… Marking down (AF11 → AF12) instead of
dropping lets the network keep excess traffic while there's room.

## Queuing (congestion management)

Queuing only matters when an interface is **congested** (packets arrive
faster than it sends).

- **FIFO**: one queue.
- **WFQ**: per-flow fair queuing.
- **CBWFQ**: a queue per class with a **bandwidth guarantee**
  (`bandwidth kbps | percent | remaining percent`).
- **LLQ** = CBWFQ + a **strict priority queue** (`priority`) served first,
  and policed to its rate under congestion so it can't starve the others.
  The queue for voice and video.
- **Hierarchical QoS (HQoS)**: a parent policy shapes (or polices) an
  aggregate, a child policy queues *within* it. The usual egress policy for
  a customer whose service is slower than its port.

## Congestion avoidance

- **Tail drop**: a full queue drops everything arriving. Many TCP flows lose
  packets at once, back off together and ramp up together: **TCP global
  synchronization**, and the link oscillates.
- **RED / WRED**: drop randomly *before* the queue is full. Between a
  **minimum** and **maximum** threshold of average queue depth, the drop
  probability rises up to the *mark probability denominator*; above the
  maximum, everything is dropped. *Weighted*: thresholds per IP precedence,
  DSCP or EXP, so higher drop precedence (AF12, AF13) is dropped first.
- **ECN**: instead of dropping, mark the ECN bits of ECN-capable packets so
  the endpoints slow down (`random-detect ecn`).
- WRED helps TCP; it does nothing useful for UDP voice, which belongs in the
  priority queue.

## IOS XE and IOS XR MQC

| | IOS XE | IOS XR |
|---|---|---|
| End a class-map / policy-map | `exit` | `end-class-map` / `end-policy-map` |
| Priority queue | `priority` / `priority percent N` / `priority level 1` | `priority level 1` (+ a policer to cap it) |
| Bandwidth | `bandwidth`, `bandwidth remaining percent` | `bandwidth`, `bandwidth remaining percent / ratio` |
| Policer | `police cir 256000 pir 384000` + `conform-action` … | `police rate 256 kbps peak-rate 384 kbps` + `conform-action` … |
| Shaper | `shape average 400000` | `shape average 400 kbps` |
| WRED | `random-detect dscp-based` | `random-detect dscp af12 10 ms 20 ms`, `random-detect default` |
| Apply | `service-policy input / output` | `service-policy input / output` |
| Verify | `show policy-map interface X [input / output]` | `show policy-map interface X`, `show qos interface X output`, `show policy-map targets` |

**In this lab:** XRd Control Plane accepts the whole configuration and
attaches output policies, but has no QoS data plane: policies report
*Service Policy not installed* and input policies are refused. The CSR 1000v
runs every feature for real, but an unlicensed CSR forwards at most **1 Mb/s**,
so the lab's flows are a few hundred kb/s.

## Verification cheat sheet

| What | IOS XE | IOS XR |
|---|---|---|
| Policy counters per class | `show policy-map interface Gi3 input` | `show policy-map interface Gi0/0/0/0 input` |
| Shaper, queues, drops | `show policy-map interface Gi3 output` | `show qos interface Gi0/0/0/1 output` |
| Where a policy is attached | `show running-config \| section service-policy` | `show policy-map targets` |
| Policy / class definitions | `show policy-map NAME`, `show class-map` | `show running-config policy-map NAME`, `show running-config class-map` |
| DSCP on arrival | the receiving traffic generator's panel (per stream, last 10 s), or a packet capture on the cable | |
| MPLS label pushed | `show mpls forwarding-table 10.1.4.0` | `show mpls forwarding` |
