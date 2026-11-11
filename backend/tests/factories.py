"""Small builders for seeding test topologies."""
from app.models.topology import Topology
from app.services.topology_ops import create_link, create_node


def make_topology(db, name="T"):
    t = Topology(name=name)
    db.add(t)
    db.flush()
    return t


def make_node(db, topo, name=None, kind="linux", image="plabs/host:latest", **kw):
    return create_node(db, topo.id, name=name, kind=kind, image_ref=image, **kw)


def make_link(db, topo, a, b, a_if=None, b_if=None):
    return create_link(db, topo.id, a.id, b.id, a_if, b_if)
