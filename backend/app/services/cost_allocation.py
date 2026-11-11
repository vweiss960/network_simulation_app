"""Cost allocation engine — distributes link circuit costs to customers based on flow utilization.

Two allocation models:
  1. "proportional_used" — cost split among customers proportional to their share of USED bandwidth.
     Unused capacity cost stays unallocated (network owner absorbs it).
  2. "proportional_total" — cost split proportional to customer's share of TOTAL link capacity.
     All capacity is allocated, including unused (distributed proportionally among users).
"""

from sqlalchemy.orm import Session
from app.models.node_instance import NodeInstance
from app.models.link import Link
from app.models.flow import Flow
from app.models.customer import Customer
from app.services.simulation import compute_flow_paths
from app.services.customer_flows import node_customer_map, flow_customer_ids


def run_cost_allocation(topology_id: int, db: Session, allocation_model: str = "proportional_used") -> dict:
    """
    Run cost allocation analysis.

    Returns a fully transparent breakdown:
    - Per-link: which customer flows traverse it, how much bandwidth each uses, cost allocation
    - Per-customer: total cost, itemized by link
    - Summary: total network cost, allocated vs unallocated
    - Formula explanation per link
    """
    nodes = db.query(NodeInstance).filter(NodeInstance.topology_id == topology_id).all()
    links = db.query(Link).filter(Link.topology_id == topology_id).all()
    flows = db.query(Flow).filter(Flow.topology_id == topology_id).all()
    customers = db.query(Customer).filter(Customer.topology_id == topology_id).all()

    node_name_map = {n.id: n.name for n in nodes}
    customer_map = {c.id: c for c in customers}
    flow_map = {f.id: f for f in flows}
    link_map = {l.id: l for l in links}
    # Customers are derived from the flow's endpoint nodes. A flow between two
    # customers' nodes has its bandwidth split evenly between them.
    node_customer = node_customer_map(nodes)
    flow_customers = {f.id: flow_customer_ids(f, node_customer) for f in flows}

    # Run simulation to get flow paths and link utilizations
    flow_paths, link_utilizations = compute_flow_paths(nodes, links, flows, db)

    # Build: link_id -> list of (flow_id, bandwidth_on_link)
    # From flow_paths, each flow's path tells us which links it traverses
    link_flow_usage = {}  # link_id -> [(flow_id, bw_gbps)]
    for fp in flow_paths:
        flow = flow_map.get(fp["flow_id"])
        if not flow:
            continue
        paths = fp.get("paths", [])
        bw_per_path = fp.get("bandwidth_per_path_gbps", [])
        link_ids_per_path = fp.get("link_ids_per_path", [])

        for path_idx, link_ids in enumerate(link_ids_per_path):
            bw = bw_per_path[path_idx] if path_idx < len(bw_per_path) else 0
            for lid in link_ids:
                if lid not in link_flow_usage:
                    link_flow_usage[lid] = []
                link_flow_usage[lid].append((flow.id, bw))

    # Calculate cost allocation per link
    link_breakdowns = []
    customer_totals = {}  # customer_id -> {cost, links: [{link_id, cost, ...}]}
    total_network_cost = 0
    total_allocated_cost = 0
    total_unallocated_cost = 0

    for link in links:
        circuit_cost = link.circuit_cost or 0
        total_network_cost += circuit_cost

        if circuit_cost <= 0:
            continue  # No cost to allocate

        src_name = node_name_map.get(link.source_node_id, "?")
        tgt_name = node_name_map.get(link.target_node_id, "?")
        link_name = f"{src_name} \u2194 {tgt_name}"

        flow_usages = link_flow_usage.get(link.id, [])

        # Aggregate by customer
        customer_bw = {}  # customer_id -> total_bw
        unassigned_bw = 0
        for flow_id, bw in flow_usages:
            cids = flow_customers.get(flow_id, [])
            if cids:
                for cid in cids:
                    customer_bw[cid] = customer_bw.get(cid, 0) + bw / len(cids)
            else:
                unassigned_bw += bw

        total_used_bw = sum(customer_bw.values()) + unassigned_bw
        total_capacity = link.bandwidth_gbps

        # Calculate allocation based on model
        link_allocations = []
        link_allocated = 0

        for cust_id, cust_bw in customer_bw.items():
            cust = customer_map.get(cust_id)
            if not cust:
                continue

            if allocation_model == "proportional_total":
                # Customer's share of total capacity
                share = cust_bw / total_capacity if total_capacity > 0 else 0
                allocated = circuit_cost * share
                formula = f"{cust_bw:.3f}G / {total_capacity:.1f}G (total capacity) \u00d7 ${circuit_cost:,.2f} = ${allocated:,.2f}"
            else:
                # proportional_used: share of used bandwidth
                share = cust_bw / total_used_bw if total_used_bw > 0 else 0
                allocated = circuit_cost * share
                formula = f"{cust_bw:.3f}G / {total_used_bw:.3f}G (used bandwidth) \u00d7 ${circuit_cost:,.2f} = ${allocated:,.2f}"

            link_allocated += allocated

            # Get the specific flows from this customer on this link
            cust_flows_on_link = []
            for flow_id, bw in flow_usages:
                f = flow_map.get(flow_id)
                cids = flow_customers.get(flow_id, [])
                if f and cust_id in cids:
                    cust_flows_on_link.append({
                        "flow_id": flow_id,
                        "flow_name": f.name,
                        "bandwidth_gbps": round(bw / len(cids), 4),
                    })

            link_allocations.append({
                "customer_id": cust_id,
                "customer_name": cust.name,
                "bandwidth_gbps": round(cust_bw, 4),
                "share_pct": round(share * 100, 2),
                "allocated_cost": round(allocated, 2),
                "formula": formula,
                "flows": cust_flows_on_link,
            })

            # Accumulate customer totals
            if cust_id not in customer_totals:
                customer_totals[cust_id] = {"cost": 0, "links": [], "customer_name": cust.name}
            customer_totals[cust_id]["cost"] += allocated
            customer_totals[cust_id]["links"].append({
                "link_id": link.id,
                "link_name": link_name,
                "bandwidth_gbps": round(cust_bw, 4),
                "share_pct": round(share * 100, 2),
                "allocated_cost": round(allocated, 2),
            })

        unallocated = circuit_cost - link_allocated
        total_allocated_cost += link_allocated
        total_unallocated_cost += unallocated

        link_breakdowns.append({
            "link_id": link.id,
            "link_name": link_name,
            "circuit_cost": circuit_cost,
            "bandwidth_gbps": total_capacity,
            "used_gbps": round(total_used_bw, 4),
            "utilization_pct": round(total_used_bw / total_capacity * 100, 1) if total_capacity > 0 else 0,
            "allocations": link_allocations,
            "unassigned_bandwidth_gbps": round(unassigned_bw, 4),
            "unallocated_cost": round(unallocated, 2),
            "allocation_model": allocation_model,
            "explanation": (
                f"Link {link_name}: circuit cost ${circuit_cost:,.2f}, "
                f"capacity {total_capacity}G, used {total_used_bw:.3f}G ({total_used_bw/total_capacity*100:.1f}%). "
                + (f"Cost split among customers by share of used bandwidth."
                   if allocation_model == "proportional_used"
                   else f"Cost split among customers by share of total capacity.")
            ) if total_capacity > 0 else f"Link {link_name}: no capacity data.",
        })

    # Build customer summary
    customer_summaries = []
    for cust_id, totals in customer_totals.items():
        customer_summaries.append({
            "customer_id": cust_id,
            "customer_name": totals["customer_name"],
            "total_cost": round(totals["cost"], 2),
            "link_count": len(totals["links"]),
            "links": totals["links"],
        })
    customer_summaries.sort(key=lambda x: -x["total_cost"])

    return {
        "allocation_model": allocation_model,
        "allocation_model_description": (
            "Proportional to Used Bandwidth: Each customer pays based on their share of "
            "the bandwidth actually in use on each link. Unused capacity cost remains unallocated."
            if allocation_model == "proportional_used"
            else "Proportional to Total Capacity: Each customer pays based on their share of "
            "the total link capacity. All circuit cost is distributed among users."
        ),
        "summary": {
            "total_network_cost": round(total_network_cost, 2),
            "total_allocated_cost": round(total_allocated_cost, 2),
            "total_unallocated_cost": round(total_unallocated_cost, 2),
            "customer_count": len(customer_summaries),
            "links_with_cost": sum(1 for l in links if (l.circuit_cost or 0) > 0),
            "total_links": len(links),
            "flows_assigned": sum(1 for cids in flow_customers.values() if cids),
            "total_flows": len(flows),
        },
        "customers": customer_summaries,
        "link_breakdowns": link_breakdowns,
    }
