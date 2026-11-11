"""Satellite dishes, satellite PoPs and the satellite links between them.

A dish (role "sat_dish") reaches one or more PoPs (role "sat_pop") over
satellite links: transport "satellite" with a per-link orbit (LEO / MEO / GEO)
and the provider in `carrier`. Satellite links route like any other link; they
need no interfaces (bandwidth and cost are entered directly). A dish may also
have ordinary links to its own site's equipment.
"""

SAT_DISH, SAT_POP = "sat_dish", "sat_pop"
SAT_ROLES = {SAT_DISH, SAT_POP}
ORBITS = ("leo", "meo", "geo")
TRANSPORTS = ("terrestrial", "satellite")


class SatelliteLinkError(ValueError):
    """A link that breaks the dish / PoP rules."""


def validate_link_roles(src_role: str | None, tgt_role: str | None,
                        transport: str | None, orbit: str | None) -> str:
    """Check a link against the satellite rules; return its transport.

    - every dish ↔ PoP link is a satellite link, and a satellite link must be
      dish ↔ PoP (with an orbit)
    - dishes can't link to each other
    """
    src, tgt = src_role or "router", tgt_role or "router"
    transport = transport or "terrestrial"
    if transport not in TRANSPORTS:
        raise SatelliteLinkError(f"Unknown transport '{transport}'")
    if src == SAT_DISH and tgt == SAT_DISH:
        raise SatelliteLinkError("Satellite dishes can't link to each other — connect a dish to a Satellite PoP")
    sat_pair = {src, tgt} == {SAT_DISH, SAT_POP}
    if transport == "satellite" or sat_pair:
        if not sat_pair:
            raise SatelliteLinkError("A satellite link must connect a Satellite Dish to a Satellite PoP")
        if (orbit or "") not in ORBITS:
            raise SatelliteLinkError("Choose the orbit (LEO, MEO or GEO) for a satellite link")
        return "satellite"
    return "terrestrial"
