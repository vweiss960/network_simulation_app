"""Helpers for walking the parent-topology inheritance chain.

A child topology inherits all nodes and links from its parent (and recursively
from the parent's parent). The child can add its own items but cannot modify
or delete inherited ones.
"""

from typing import Optional
from sqlalchemy.orm import Session
from app.models.topology import Topology


def get_topology_chain(db: Session, topology_id: int) -> list[int]:
    """Return ordered list of topology IDs from root to self.

    Example: if topology 5's parent is 3, and 3's parent is 1, this returns
    [1, 3, 5] when called with topology_id=5.

    Stops gracefully if a cycle is detected (which should be prevented at
    write time, but we defend against bad data).
    """
    chain: list[int] = []
    seen: set[int] = set()
    current: Optional[int] = topology_id
    while current is not None and current not in seen:
        seen.add(current)
        chain.append(current)
        topo = db.query(Topology).filter(Topology.id == current).first()
        if topo is None:
            break
        current = topo.parent_topology_id
    chain.reverse()
    return chain


def would_create_cycle(db: Session, topology_id: int, proposed_parent_id: int) -> bool:
    """Check whether setting proposed_parent_id as the parent of topology_id
    would introduce a cycle.

    A cycle exists if topology_id appears anywhere in the proposed parent's
    own ancestry chain (or if proposed_parent_id == topology_id).
    """
    if proposed_parent_id == topology_id:
        return True
    return topology_id in get_topology_chain(db, proposed_parent_id)


def merge_by_name(items: list, chain: list[int]) -> tuple[list, dict[int, int]]:
    """Given items pulled from the inheritance chain, deduplicate by name with
    the highest ancestor winning. Returns (visible, remap):

      - visible: list of items that should be shown in the merged view
        (items whose name is not already claimed by a higher ancestor)
      - remap: dict mapping each shadowed item's id → the winning ancestor's id

    Used to silently absorb child-local nodes/flows when a parent later adds
    one with the same name. Each item must have .id, .name, .topology_id.
    """
    chain_pos = {tid: i for i, tid in enumerate(chain)}
    # Sort root → self so the first occurrence of a name wins.
    sorted_items = sorted(items, key=lambda it: chain_pos.get(it.topology_id, 10**9))
    name_winner: dict[str, int] = {}
    visible: list = []
    remap: dict[int, int] = {}
    for it in sorted_items:
        if it.name not in name_winner:
            name_winner[it.name] = it.id
            visible.append(it)
        else:
            remap[it.id] = name_winner[it.name]
    return visible, remap


def get_nullified_child_link_ids(db: Session, topology_id: int) -> set[int]:
    """Return IDs of links in this topology's chain that should be hidden
    because a HIGHER topology in the chain already has a link between the same
    pair of nodes — the highest ancestor's link always wins.

    Used during the planning workflow: planning-phase links between two nodes
    are silently nullified once a parent commits an "official" link between
    those same nodes (regardless of interface choice). This applies at every
    level of the chain, not just to this topology's own links: viewing a
    grandchild must not resurrect a middle layer's link that the grandparent
    absorbed. Node-shadowing is respected — if an endpoint has been absorbed
    by a higher same-named node, the remapped pair is what's compared.
    """
    from app.models.node_instance import NodeInstance
    from app.models.link import Link

    chain = get_topology_chain(db, topology_id)
    if len(chain) <= 1:
        return set()
    all_nodes = db.query(NodeInstance).filter(NodeInstance.topology_id.in_(chain)).all()
    _, node_remap = merge_by_name(all_nodes, chain)
    links = db.query(Link).filter(Link.topology_id.in_(chain)).all()
    return nullified_link_ids(chain, links, node_remap)


def nullified_link_ids(chain: list[int], links: list, node_remap: dict[int, int]) -> set[int]:
    """get_nullified_child_link_ids over already-loaded chain data. `links` are
    every link in the chain with their ORIGINAL (un-remapped) endpoints."""
    def _pair(l) -> frozenset:
        s = node_remap.get(l.source_node_id, l.source_node_id)
        t = node_remap.get(l.target_node_id, l.target_node_id)
        return frozenset((s, t))

    links_by_topo: dict[int, list] = {tid: [] for tid in chain}
    for l in links:
        links_by_topo.setdefault(l.topology_id, []).append(l)

    # Walk root → self: a level's link is hidden when any higher level already
    # links the same pair; parallel links within one level all stay.
    higher_pairs: set[frozenset] = set()
    nullified: set[int] = set()
    for i, tid in enumerate(chain):
        level = links_by_topo[tid]
        if i > 0:
            nullified |= {l.id for l in level if _pair(l) in higher_pairs}
        higher_pairs |= {_pair(l) for l in level}
    return nullified


def get_merged_topology_data(db: Session, topology_id: int) -> tuple[list, list, list, dict[int, int]]:
    """Return (nodes, links, flows, node_remap) for the merged inheritance view:

      - shadowed nodes and flows are removed
      - link/flow source/target IDs are remapped to point at the winning ancestor
        when a shadowed local would otherwise be referenced

    All returned ORM objects are detached from the session via expunge so that
    simulation code can mutate fields like source_node_id without risk of an
    accidental DB write. Callers MUST NOT pass these objects back to db.commit()
    or use them for persistence — use them as read-only snapshots only.
    """
    # Local imports to avoid circular dependencies at module load time.
    from app.models.node_instance import NodeInstance
    from app.models.link import Link
    from app.models.flow import Flow

    chain = get_topology_chain(db, topology_id)
    all_nodes = db.query(NodeInstance).filter(NodeInstance.topology_id.in_(chain)).all()
    visible_nodes, node_remap = merge_by_name(all_nodes, chain)

    all_links = db.query(Link).filter(Link.topology_id.in_(chain)).all()
    all_flows = db.query(Flow).filter(Flow.topology_id.in_(chain)).all()
    visible_flows, _ = merge_by_name(all_flows, chain)

    # Links absorbed by a higher topology's link between the same node pair
    # are excluded from the merged view entirely (computed before the remap
    # below mutates endpoints).
    hidden_link_ids = nullified_link_ids(chain, all_links, node_remap)

    # Detach so in-memory edits do not accidentally persist when the session closes
    for n in all_nodes:
        db.expunge(n)
    for l in all_links:
        db.expunge(l)
    for f in all_flows:
        db.expunge(f)

    # Remap endpoints to the winning ancestor's ID
    if node_remap:
        for l in all_links:
            if l.source_node_id in node_remap:
                l.source_node_id = node_remap[l.source_node_id]
            if l.target_node_id in node_remap:
                l.target_node_id = node_remap[l.target_node_id]
        for f in all_flows:
            if f.source_node_id in node_remap:
                f.source_node_id = node_remap[f.source_node_id]
            if f.dest_node_id in node_remap:
                f.dest_node_id = node_remap[f.dest_node_id]

    visible_links = [l for l in all_links if l.id not in hidden_link_ids]

    return visible_nodes, visible_links, visible_flows, node_remap
