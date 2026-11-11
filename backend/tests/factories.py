"""Small builders for seeding test topologies. Each flushes so .id is set."""
import json

from app.models.topology import Topology
from app.models.node_instance import NodeInstance
from app.models.link import Link
from app.models.flow import Flow


def make_topology(db, name="T", parent_id=None):
    t = Topology(name=name, parent_topology_id=parent_id)
    db.add(t)
    db.flush()
    return t


def make_node(db, topo, name, role="router", planned=False, interfaces=None, **kw):
    n = NodeInstance(
        topology_id=topo.id, name=name, role=role,
        is_planned=1 if planned else 0,
        interfaces_json=json.dumps(interfaces or []), **kw,
    )
    db.add(n)
    db.flush()
    return n


def make_link(db, topo, src, tgt, bw=10.0, cost=1.0, latency=1.0, planned=False, **kw):
    """latency=None makes an unknown-latency link (estimated from geography)."""
    kw.setdefault("latency_source", "measured" if latency is not None else "unknown")
    l = Link(
        topology_id=topo.id, source_node_id=src.id, target_node_id=tgt.id,
        bandwidth_gbps=bw, cost=cost, latency_ms=latency if latency is not None else 0.0,
        is_planned=1 if planned else 0, **kw,
    )
    db.add(l)
    db.flush()
    return l


def make_flow(db, topo, name, src, dst, bw=1.0, routing="shortest_path", ecmp=False):
    f = Flow(
        topology_id=topo.id, name=name, source_node_id=src.id, dest_node_id=dst.id,
        bandwidth_gbps=bw, routing_behavior=routing, ecmp_enabled=1 if ecmp else 0,
    )
    db.add(f)
    db.flush()
    return f
