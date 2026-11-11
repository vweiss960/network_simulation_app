"""Flow -> customer membership.

A flow belongs to a customer when either end terminates on a customer-role node
assigned to that customer (NodeInstance.customer_id). A stale customer_id left on
a node switched back to role="router" is ignored. Membership is derived from the nodes,
never stored: a flow between two customers' nodes belongs to both. The legacy
Flow.customer_id column is ignored.
"""


def node_customer_map(nodes) -> dict[int, int]:
    """node_id -> customer_id for every customer-role node assigned to a customer."""
    return {n.id: n.customer_id for n in nodes if n.customer_id and n.role == "customer"}


def flow_customer_ids(flow, node_customer: dict[int, int]) -> list[int]:
    """Distinct customers owning the flow's endpoints, A-end first."""
    ids: list[int] = []
    for nid in (flow.source_node_id, flow.dest_node_id):
        cid = node_customer.get(nid)
        if cid and cid not in ids:
            ids.append(cid)
    return ids
