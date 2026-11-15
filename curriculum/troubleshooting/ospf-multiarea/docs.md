## Network overview

Two sites joined by a three-router OSPF backbone, IPv4 (OSPFv2, process 1)
and IPv6 (OSPFv3, process 1) side by side.

| Router | OS | Role | Loopback / router ID |
|---|---|---|---|
| R1 | IOS XR | ABR area 0 / area 1 | 1.1.1.1 · 2001:db8::1 |
| R2 | IOS XR | Backbone; ASBR for the customer network 203.0.113.0/24 | 2.2.2.2 · 2001:db8::2 |
| R3 | IOS XE | ABR area 0 / area 2 | 3.3.3.3 · 2001:db8::3 |
| R4 | IOS XE | Site 1 router, area 1 | 4.4.4.4 · 2001:db8:1::4 |
| R5 | IOS XR | Site 2 router, area 2; ASBR for the partner network 198.51.100.0/24 | 5.5.5.5 · 2001:db8:2::5 |

## Areas

| Area | Type | Addressing | ABR summarises to |
|---|---|---|---|
| 0 | backbone | 10.0.0.0/16, 2001:db8:0::/48 | — |
| 1 | **totally stubby** (`stub no-summary` on R1, `stub` on R4) | 10.1.0.0/16, 2001:db8:1::/48 | 10.1.0.0/16 · 2001:db8:1::/48 |
| 2 | **NSSA** | 10.2.0.0/16, 2001:db8:2::/48 | 10.2.0.0/16 · 2001:db8:2::/48 |

- Every router-to-router link is **point-to-point**; loopbacks and the site
  LANs are passive.
- Reference bandwidth **100 Gb/s** everywhere (gigabit links cost 100). The
  R1–R3 link is a **backup**: cost 1000, so R1 ↔ R3 traffic normally goes
  through R2.
- Default hello / dead timers, no authentication.

## Links and external networks

| Link | IPv4 | IPv6 |
|---|---|---|
| R1–R2 | 10.0.12.0/30 | 2001:db8:0:12::/64 |
| R2–R3 | 10.0.23.0/30 | 2001:db8:0:23::/64 |
| R1–R3 (backup) | 10.0.13.0/30 | 2001:db8:0:13::/64 |
| R1–R4 | 10.1.14.0/30 | 2001:db8:1:14::/64 |
| R3–R5 | 10.2.35.0/30 | 2001:db8:2:35::/64 |
| Site 1 LAN (R4, H1 = .10) | 10.1.4.0/24 | 2001:db8:1:4::/64 |
| Site 2 LAN (R5, H2 = .10) | 10.2.5.0/24 | 2001:db8:2:5::/64 |

| External network | Where | Into OSPF as |
|---|---|---|
| 203.0.113.0/24 · 2001:db8:cafe::/48 (customer) | static on R2 | type 5, E2 |
| 198.51.100.0/24 · 2001:db8:beef::/48 (partner) | static on R5 | type 7 (N2), translated to type 5 by R3 |
