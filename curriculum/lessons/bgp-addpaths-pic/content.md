## Why BGP converges slowly

When a link fails, an IGP reconverges in well under a second. BGP can take
much longer:

1. The failure has to be **detected** — interface down, BFD, or a hold timer
   (180 s by default!).
2. The router withdraws its paths; the withdrawal **propagates** through the
   route reflectors.
3. Every router runs best-path again — **per prefix**. With a full Internet
   table that's around a million prefixes to recompute and reprogram into
   the FIB, one by one.

Two things make that worse in a provider network:

- **Route reflectors hide paths.** A reflector advertises only its best
  path, so clients often know a single exit per prefix. When it fails they
  must wait for the reflector to compute and send the next one.
- **The FIB is flat.** Without a hierarchy, each of the million prefixes
  points to its own copy of the next-hop information, and each must be
  rewritten.

## Additional paths

**Additional paths** (RFC 7911) lets a BGP speaker advertise several paths
for the same prefix. Each path carries a **path identifier**, so a new path
doesn't implicitly replace the previous one.

- It's a **capability**: both ends must advertise it (send / receive), and
  capabilities are exchanged only when a session **starts** — reset the
  session after enabling it.
- The sender chooses **which** extra paths to advertise — on IOS XR with a
  route policy (`set path-selection backup 1 advertise`, or `all`,
  `multipath`), on IOS XE with `bgp additional-paths select` options.

Alternatives with the same goal: **diverse paths** (a second reflector
session that carries the second-best path), and **unique route
distinguishers per PE** in L3VPN, which make the paths look like different
prefixes.

```
! IOS XR — route reflector
route-policy ADD-PATHS
  set path-selection backup 1 advertise
end-policy
router bgp 65000
 address-family ipv4 unicast
  additional-paths receive
  additional-paths send
  additional-paths selection route-policy ADD-PATHS

! IOS XE — client
router bgp 65000
 address-family ipv4
  bgp additional-paths receive
  neighbor 2.2.2.2 additional-paths receive
```

## BGP PIC

**BGP Prefix Independent Convergence** removes the per-prefix work from the
failover:

- **Hierarchical FIB.** Prefixes don't point to an outgoing interface
  directly; they point to a shared **BGP next-hop (path-list) object**,
  which points to the IGP path. A million prefixes behind one PE share one
  object.
- **PIC core.** When the **IGP path** to a next hop changes, only the shared
  object is updated — every prefix behind it moves at once. On by default
  on modern platforms.
- **PIC edge.** When the **next hop itself** dies (the PE, or its eBGP
  link), the FIB already holds a **pre-computed backup next hop** for the
  path-list, and switches to it at once. BGP reconverges afterwards and
  installs the new best path, with no traffic lost meanwhile.

PIC edge needs a backup path to install: that's why it goes together with
**add-paths** (or best-external, or unique RDs) in a route-reflector network.

```
! IOS XR — select a backup and install it
route-policy PIC
  set path-selection backup 1 install
end-policy
router bgp 65000
 address-family ipv4 unicast
  additional-paths selection route-policy PIC

! IOS XE
router bgp 65000
 address-family ipv4
  bgp additional-paths install
```

How fast does the backup take over? The FIB switch is immediate, but the
failure must be **detected** first: interface down for a local link, **BFD**
for a neighbor or remote PE, or **BGP next-hop tracking** when the IGP
withdraws a PE's loopback.

## Best-external

A router advertises only its **best** path. When policy makes an edge router
prefer an **internal** path — say local preference sends everything to the
other exit — that router stops advertising its own **external** path. The
alternative disappears from the AS, and there's nothing left for add-paths
or PIC to use.

**Best-external** makes an edge router keep advertising its best external
path even when its overall best is internal:

| | IOS XR | IOS XE |
|---|---|---|
| Command | `advertise best-external` (per address family) | `bgp advertise-best-external` (per address family) |

The external path is advertised to iBGP peers only (never back out to eBGP),
and it stays the backup at that router too.

## Verification commands

| What | IOS XR | IOS XE |
|---|---|---|
| Add-paths negotiated | `show bgp neighbor X` → *Additional-paths operation* | `show bgp ipv4 unicast neighbors X` → *Additional Paths … capability* |
| Every path for a prefix | `show bgp ipv4 unicast <prefix>` (`backup`, `add-path`) | `show bgp ipv4 unicast <prefix>` (`backup/repair`) |
| Installed backup | `show route <prefix>` → *BGP backup path* | `show ip cef <prefix> detail` → *repair* |
| Best-external in use | `show bgp ipv4 unicast <prefix>` | `show bgp ipv4 unicast <prefix>` → *advertise-best-external* |

## Wrap-up and exam tips

- Reflectors advertise only the best path → clients lack a backup.
- Add-paths: a capability, negotiated at session start; the sender selects
  which extra paths to advertise; each path has a path identifier.
- PIC core: hierarchical FIB, shared next-hop objects, on by default. PIC
  edge: a pre-installed backup next hop, needs add-paths / best-external /
  unique RDs.
- Best-external keeps an edge's external path advertised when policy prefers
  an internal one.
- Fast detection (BFD, next-hop tracking) is what makes PIC fast in practice.
