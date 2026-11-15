## Network overview

AS 65000 is a small service provider:

| Router | OS | Role | Loopback |
|---|---|---|---|
| R1 | IOS XR | Edge: customer A (AS 65100) and upstream B (AS 65300) | 1.1.1.1 · 2001:db8::1 |
| R2 | IOS XR | Core, BGP route reflector | 2.2.2.2 · 2001:db8::2 |
| R3 | IOS XE | Edge: upstream A (AS 65200), a static customer | 3.3.3.3 · 2001:db8::3 |

R4 (customer A), R5 (upstream A) and R6 (upstream B) belong to other
organisations: you can log in and look, but their configuration is correct.

## IGP: IS-IS

- Instance **CORE**, every core router **level-2-only**, area addresses
  49.0000.
- Every core link is **point-to-point**, IPv4 and IPv6, **wide** metrics,
  IPv6 in its own topology (multi-topology). All link metrics 10.
- Loopbacks are passive. Edge links to other ASes are **not** in IS-IS.

| Link | IPv4 | IPv6 |
|---|---|---|
| R1–R2 | 10.0.12.0/30 | 2001:db8:0:12::/64 |
| R2–R3 | 10.0.23.0/30 | 2001:db8:0:23::/64 |
| R1–R3 | 10.0.13.0/30 | 2001:db8:0:13::/64 |

## BGP

- iBGP between loopbacks, separate IPv4 and IPv6 sessions, **R2 reflects**
  to its clients R1 and R3. The edges set `next-hop-self` towards R2.
- eBGP on the directly connected addresses:

| Session | Peer AS | IPv4 | IPv6 |
|---|---|---|---|
| R1 – customer A (R4) | 65100 | 10.1.14.0/30 | 2001:db8:1:14::/64 |
| R1 – upstream B (R6) | 65300 | 10.3.16.0/30 | 2001:db8:3:16::/64 |
| R3 – upstream A (R5) | 65200 | 10.2.35.0/30 | 2001:db8:2:35::/64 |

- IOS XR eBGP sessions use the pass-all route policy `PASS` in both
  directions.
- AS 65000 originates **100.64.0.0/16** and **2001:db8:6500::/48** from R1
  and R3 (static routes to Null0 + `network`).
- A static customer behind R3, **100.70.20.0/24** / **2001:db8:c2::/48**,
  is redistributed through the route maps `STATIC-CUST` / `STATIC-CUST-V6`.

## Who's who

| | Prefixes |
|---|---|
| Customer A (AS 65100) | 192.0.2.0/24 (H1 = 192.0.2.10), 2001:db8:c1::/48 |
| Upstream A (AS 65200) | 198.51.100.0/24 (server H2 = 198.51.100.10), 2001:db8:a5::/48, 5.5.5.5 |
| Upstream B (AS 65300) | 6.6.6.6, 2001:db8:b6::/48 |
| Both upstreams (anycast) | 203.0.113.0/24 (203.0.113.1), 2001:db8:ffff::/48 |
