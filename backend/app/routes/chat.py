import json
import logging
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.models.database import get_db
from app.models.node_instance import NodeInstance
from app.models.node_template import NodeTemplate
from app.models.link import Link
from app.services.latency import infer_latency_source, latency_infos
from app.models.flow import Flow
from app.models.topology import Topology
from app.services.simulation import run_simulation, run_monte_carlo
from app.services import llm
from app.services.customer_flows import node_customer_map, flow_customer_ids

router = APIRouter(prefix="/api/topologies/{topology_id}/chat", tags=["chat"])

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are Phanero AI, a tool operator built into a network modeling application. Your role is to execute user commands efficiently using the tools available to you.

**Your role — DO:**
- Execute actions the user asks for: add/remove/update nodes, links, flows
- Look up information when asked: topology state, settings, documentation
- Highlight and focus elements when asked to show or find things
- Generate reports and switch views when requested
- Answer "how do I..." questions using the get_app_help tool
- Present data and results clearly and concisely

**Your role — DO NOT:**
- Offer unsolicited suggestions or recommendations
- Make engineering judgments or optimization decisions
- Interpret results or draw conclusions — present the data and let the user decide
- Run simulations after adding/removing elements (they auto-run). DO run simulations when you need utilization, SPOF, or flow path data to answer a question.
- Modify link cost values (use update_flow routing_behavior instead)
- Take multiple actions when the user asked for one

**Show vs Tell:**
- "show me", "highlight", "find", "where" → use visual tools (highlight_elements, focus_element, set_color_scheme) first, then brief text
- "tell me", "list", "what are" → text response only
- When ambiguous, prefer showing over telling
- When running SPOF analysis, always highlight the SPOFs using their IDs

**Accuracy rules:**
- NEVER fabricate, guess, or assume IDs, names, or values. Always fetch real data from the topology first using get_topology_info or run_simulation before acting. If you don't have the data yet, say so and fetch it — do not make up placeholder IDs or approximate answers.
- Before performing bulk actions (updating multiple flows, highlighting multiple elements), ALWAYS count the items first and state the count: "Found N flows matching your criteria. Updating all N."
- After completing bulk actions, verify the count: "Updated N of N flows." If the counts don't match, say so.
- Never assume you've found all items — double-check by re-reading the data if the task involves "all" or "every".

**Keep responses short.** State what you did and the key data points. Do not add analysis, interpretation, or follow-up suggestions unless the user asks.

Key concepts in this tool:
- **Nodes**: Network devices (routers/switches) or customer endpoints. Each has interfaces with speeds, MTBF/MTTR values. Nodes have a **role**: 'router' (default, network device) or 'customer' (customer connection point over a last-mile link — MTTR=0, doesn't impact availability). Customer nodes can be assigned to a Customer, which auto-assigns all flows touching that node to the customer.
- **Links**: Connections between node interfaces. Have bandwidth, cost, latency, carrier, MTBF/MTTR, SR attributes, and an optional custom color (hex). Links can be colored individually or in bulk to visually group circuits (e.g. by carrier or region).
- **Flows/Demands**: Traffic from A (source) to Z (destination). Support routing behaviors: shortest_path, lowest_latency, optimized (dynamic cost based on utilization), explicit path. ECMP splits traffic across equal-cost paths.
- **Simulation**: Computes flow paths, link utilization, identifies single points of failure (SPOF), and tests every node/link failure scenario.
- **Monte Carlo**: Runs thousands of random failure iterations using MTBF/MTTR to compute availability (single-homed and dual-homed) and annual downtime.
- **Color schemes**: Links can be colored by utilization (blue→green→red), cost/GB (yellow→purple), or criticality (green→red).
- **MTBF**: Mean Time Between Failures (hours). Higher = more reliable.
- **MTTR**: Mean Time To Repair (hours). Lower = faster recovery.

When a user asks how to do something or where to find a feature, use the get_app_help tool to look up documentation. This gives you detailed UI descriptions including button locations, icon types, and step-by-step instructions. Use topic='search' with a keyword if you're not sure which topic to look up.

When adding nodes, always use a device template when possible — list them first with get_topology_info to see available templates.
When creating links, you need source and target node IDs.
When creating flows, you need source and destination node IDs and a name.

**Planned vs Active infrastructure:**
- When the user asks you to add a node or link, ask whether it should be "planned" (not yet built) or "active" (currently exists) — UNLESS the context makes it clear:
  - Phrases like "what if we add", "let's see what happens", "what would it look like", "propose", "plan" → create as PLANNED (is_planned=true)
  - Phrases like "add", "create", "connect", "build" without hypothetical language → ask the user
  - Phrases like "this exists", "we have", "it's already there" → create as ACTIVE (is_planned=false)
- CRITICAL: When you create planned elements, you MUST call set_topology_toggles with hide_planned=false AND include_planned=true so the user can see the planned elements and the simulation includes them. Do this BEFORE any other actions.
- Do NOT run a simulation after adding planned elements — the app auto-runs simulations when include_planned is toggled on.

**Maintenance & What-If Scenarios:**
- You can run what-if scenarios to test the impact of removing nodes or links from the network. This shows which flows break, which get rerouted, capacity impact, and utilization changes.
- You can list and save maintenance presets — named failure scenarios that can be quickly re-applied.
- When a user asks "what happens if X goes down" or "what's the impact of removing Y", use the run_what_if tool proactively.

**Discovery & Prometheus:**
- You can run network discovery from a seed device using IGP (ISIS/OSPF) or LLDP protocols. After discovery, Prometheus SNMP data automatically enriches the topology with real interface names, speeds, and IP-based link correlation.
- You can configure Prometheus, check its status, discover SNMP targets, and diagnose data issues.
- When troubleshooting discovery or Prometheus issues, be specific and actionable in your guidance. Tell the user exactly what's missing and how to fix it (e.g., "LLDP is not enabled on PE3 — run 'lldp' in global config on IOS-XR to enable it").
- Use check_prometheus_status and diagnose_prometheus_data proactively when the user reports issues with discovery or live metrics."""


TOOLS = [
    {
        "name": "get_topology_info",
        "description": "Get complete information about the current topology including all nodes, links, flows, and available device templates. Use this first to understand the current state before making changes.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "add_node",
        "description": "Add a new network device (node) to the topology. Optionally use a template_id to inherit vendor, model, interfaces, and default MTBF/MTTR values from a device template. Set is_planned=true for hypothetical/what-if nodes. Set is_planned=false ONLY when user confirms the node already exists.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Unique name for the node (e.g. 'core-rtr-01', 'edge-sw-nyc')"},
                "template_id": {"type": "integer", "description": "ID of device template to use (get from get_topology_info)"},
                "latitude": {"type": "number", "description": "Geographic latitude (e.g. 40.7128 for NYC). Determines canvas position."},
                "longitude": {"type": "number", "description": "Geographic longitude (e.g. -74.0060 for NYC). Determines canvas position."},
                "vendor": {"type": "string", "description": "Override vendor (e.g. 'Cisco', 'Juniper')"},
                "model": {"type": "string", "description": "Override model (e.g. 'ASR 9001')"},
                "platform": {"type": "string", "description": "Override platform (e.g. 'iosxr', 'junos')"},
                "mtbf_hours": {"type": "number", "description": "Override MTBF in hours"},
                "mttr_hours": {"type": "number", "description": "Override MTTR in hours"},
                "location": {"type": "string", "description": "Physical location name (e.g. 'NYC', 'LAX', 'Dallas-DC1'). Used for Monte Carlo location-based availability analysis."},
                "is_planned": {"type": "boolean", "description": "IMPORTANT: Set true for hypothetical/what-if scenarios (DEFAULT when user says 'what if', 'let's see', 'what happens', 'propose', 'try'). Set false ONLY when user confirms node already exists. When in doubt, use true.", "default": True},
                "role": {"type": "string", "enum": ["router", "customer"], "description": "Node role. 'router' for network devices (default). 'customer' for customer connection points over last-mile links — customer nodes have MTTR=0 so they don't impact availability numbers."},
                "customer_id": {"type": "integer", "description": "Assign this node to a customer (by customer ID). Only meaningful for nodes with role='customer'. Flows that start or end on this node belong to that customer."},
            },
            "required": ["name"],
        },
    },
    {
        "name": "remove_node",
        "description": "Remove a node from the topology by its ID. This also removes all links connected to this node.",
        "input_schema": {
            "type": "object",
            "properties": {
                "node_id": {"type": "integer", "description": "ID of the node to remove"},
            },
            "required": ["node_id"],
        },
    },
    {
        "name": "add_link",
        "description": "Create a link between two nodes. REQUIRES specifying interfaces on both nodes. The interfaces must have matching speeds (e.g. both 10G or both 100G). Bandwidth is automatically set from the interface speed. Use get_topology_info first to see available interfaces on each node. Set is_planned=true for hypothetical/what-if links (DEFAULT for phrases like 'what if', 'let's see', 'what happens'). Set is_planned=false ONLY when the user confirms the link already exists or explicitly says to create it as active.",
        "input_schema": {
            "type": "object",
            "properties": {
                "source_node_id": {"type": "integer", "description": "ID of the source node"},
                "target_node_id": {"type": "integer", "description": "ID of the target node"},
                "source_interface": {"type": "string", "description": "REQUIRED: Source interface name (e.g. 'TenGigE0/0/0/0', 'et-0/0/0'). Must be an available (unused) interface on the source node."},
                "target_interface": {"type": "string", "description": "REQUIRED: Target interface name. Must match the source interface speed and be available (unused) on the target node."},
                "cost": {"type": "number", "description": "Link cost metric for routing (default 1)"},
                "latency_ms": {"type": "number", "description": "Known link latency in milliseconds. Omit if unknown — it is then estimated from the map."},
                "carrier": {"type": "string", "description": "Carrier/provider name"},
                "mtbf_hours": {"type": "number", "description": "Link MTBF in hours"},
                "mttr_hours": {"type": "number", "description": "Link MTTR in hours"},
                "circuit_cost": {"type": "number", "description": "Monthly circuit cost in dollars"},
                "circuit_id": {"type": "string", "description": "Circuit ID / provider reference"},
                "link_id_label": {"type": "string", "description": "Custom link identifier"},
                "is_planned": {"type": "boolean", "description": "IMPORTANT: Set true for hypothetical/what-if scenarios (DEFAULT when user says 'what if', 'let's see', 'what happens', 'propose', 'try'). Set false ONLY when user confirms link already exists. When in doubt, use true.", "default": True},
                "color": {"type": "string", "description": "Custom link color as hex (e.g. '#ef4444' for red, '#3b82f6' for blue). Used to visually group or distinguish links on the canvas."},
            },
            "required": ["source_node_id", "target_node_id", "source_interface", "target_interface"],
        },
    },
    {
        "name": "remove_link",
        "description": "Remove a link from the topology by its ID.",
        "input_schema": {
            "type": "object",
            "properties": {
                "link_id": {"type": "integer", "description": "ID of the link to remove"},
            },
            "required": ["link_id"],
        },
    },
    {
        "name": "add_flow",
        "description": "Add a traffic flow/demand between two nodes (A-end to Z-end). Specify bandwidth and routing behavior.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Name for the flow (e.g. 'NYC-to-LAX-prod')"},
                "source_node_id": {"type": "integer", "description": "Source (A-end) node ID"},
                "dest_node_id": {"type": "integer", "description": "Destination (Z-end) node ID"},
                "bandwidth_gbps": {"type": "number", "description": "Bandwidth demand in Gbps (default 1)"},
                "routing_behavior": {
                    "type": "string",
                    "enum": ["shortest_path", "lowest_latency", "optimized", "explicit"],
                    "description": "Routing algorithm: shortest_path (by cost), lowest_latency, optimized (dynamic cost based on utilization), explicit (user-defined path)",
                },
                "ecmp_enabled": {"type": "boolean", "description": "Enable Equal-Cost Multi-Path routing (default false)"},
            },
            "required": ["name", "source_node_id", "dest_node_id"],
        },
    },
    {
        "name": "update_flow",
        "description": "Update properties of an existing flow/demand. Use this to change the routing algorithm, bandwidth, source/destination, ECMP setting, or customer assignment.",
        "input_schema": {
            "type": "object",
            "properties": {
                "flow_id": {"type": "integer", "description": "ID of the flow to update"},
                "name": {"type": "string"},
                "routing_behavior": {
                    "type": "string",
                    "enum": ["shortest_path", "lowest_latency", "optimized"],
                    "description": "Routing algorithm: shortest_path (by cost metric), lowest_latency (by latency), optimized (dynamic cost based on current utilization)",
                },
                "bandwidth_gbps": {"type": "number"},
                "source_node_id": {"type": "integer", "description": "Change source (A-end) node"},
                "dest_node_id": {"type": "integer", "description": "Change destination (Z-end) node"},
                "ecmp_enabled": {"type": "boolean", "description": "Enable/disable Equal-Cost Multi-Path"},
            },
            "required": ["flow_id"],
        },
    },
    {
        "name": "bulk_update_flows",
        "description": "Update multiple flows at once. More reliable than calling update_flow repeatedly. Use this for 'change all flows from site X' or 'set all flows to optimized'. Provide a filter to match flows and the updates to apply.",
        "input_schema": {
            "type": "object",
            "properties": {
                "filter_source_node_id": {"type": "integer", "description": "Only update flows with this source node ID"},
                "filter_dest_node_id": {"type": "integer", "description": "Only update flows with this destination node ID"},
                "filter_source_or_dest_node_id": {"type": "integer", "description": "Update flows where this node is EITHER source OR destination"},
                "filter_customer_id": {"type": "integer", "description": "Only update flows belonging to this customer (a flow belongs to a customer when its source or destination node is assigned to that customer)"},
                "filter_routing_behavior": {"type": "string", "description": "Only update flows with this current routing behavior"},
                "set_routing_behavior": {"type": "string", "enum": ["shortest_path", "lowest_latency", "optimized"], "description": "New routing behavior to apply"},
                "set_ecmp_enabled": {"type": "boolean", "description": "New ECMP setting to apply"},
                "set_bandwidth_gbps": {"type": "number", "description": "New bandwidth to apply"},
            },
            "required": [],
        },
    },
    {
        "name": "remove_flow",
        "description": "Remove a flow/demand from the topology by its ID.",
        "input_schema": {
            "type": "object",
            "properties": {
                "flow_id": {"type": "integer", "description": "ID of the flow to remove"},
            },
            "required": ["flow_id"],
        },
    },
    {
        "name": "update_link",
        "description": "Update properties of an existing link, including moving either endpoint to a different node or interface. Specify source_node_id/target_node_id and/or source_interface/target_interface to change endpoints (interfaces must match speeds). NEVER modify the 'cost' routing metric — to change how a flow routes, use update_flow to change its routing_behavior instead.",
        "input_schema": {
            "type": "object",
            "properties": {
                "link_id": {"type": "integer", "description": "ID of the link to update"},
                "source_node_id": {"type": "integer", "description": "New source node ID (to re-home the link to a different node)"},
                "target_node_id": {"type": "integer", "description": "New target node ID (to re-home the link to a different node)"},
                "source_interface": {"type": "string", "description": "New source interface name. Must be available on the source node and match the target interface speed."},
                "target_interface": {"type": "string", "description": "New target interface name. Must be available on the target node and match the source interface speed."},
                "bandwidth_gbps": {"type": "number"},
                "latency_ms": {"type": "number"},
                "carrier": {"type": "string"},
                "mtbf_hours": {"type": "number"},
                "mttr_hours": {"type": "number"},
                "circuit_cost": {"type": "number", "description": "Monthly circuit cost in dollars"},
                "circuit_id": {"type": "string", "description": "Circuit ID / provider reference"},
                "link_id_label": {"type": "string", "description": "Custom link identifier"},
                "source_ip": {"type": "string", "description": "Source interface IP address"},
                "target_ip": {"type": "string", "description": "Target interface IP address"},
                "is_planned": {"type": "boolean", "description": "Mark as planned (not yet built)"},
                "color": {"type": "string", "description": "Custom link color as hex (e.g. '#ef4444' for red). Set to empty string '' to clear."},
            },
            "required": ["link_id"],
        },
    },
    {
        "name": "bulk_update_links",
        "description": "Update multiple links at once. Use this to apply the same color, carrier, or other property to many links efficiently. Provide link_ids to target and the updates to apply.",
        "input_schema": {
            "type": "object",
            "properties": {
                "link_ids": {"type": "array", "items": {"type": "integer"}, "description": "IDs of links to update"},
                "color": {"type": "string", "description": "Color to apply (hex, e.g. '#22c55e'). Use '' to clear."},
                "carrier": {"type": "string", "description": "Carrier name to apply"},
                "circuit_cost": {"type": "number", "description": "Circuit cost to apply"},
            },
            "required": ["link_ids"],
        },
    },
    {
        "name": "update_node",
        "description": "Update properties of an existing node.",
        "input_schema": {
            "type": "object",
            "properties": {
                "node_id": {"type": "integer", "description": "ID of the node to update"},
                "name": {"type": "string"},
                "vendor": {"type": "string"},
                "model": {"type": "string"},
                "platform": {"type": "string"},
                "location": {"type": "string", "description": "Physical location (e.g. 'NYC', 'LAX')"},
                "mtbf_hours": {"type": "number"},
                "mttr_hours": {"type": "number"},
                "capacity_gbps": {"type": "number"},
                "prometheus_id": {"type": "string", "description": "Prometheus instance label (IP/hostname)"},
                "mgmt_ip": {"type": "string", "description": "Management interface IP address"},
                "is_planned": {"type": "boolean", "description": "Mark as planned (not yet built)"},
                "role": {"type": "string", "enum": ["router", "customer"], "description": "Node role. 'router' for network devices, 'customer' for customer connection points."},
                "customer_id": {"type": "integer", "description": "Assign this node to a customer (by customer ID). Set to 0 to unassign. Flows that start or end on this node belong to that customer."},
            },
            "required": ["node_id"],
        },
    },
    {
        "name": "run_simulation",
        "description": "Run a full network simulation. Simulations auto-run on topology changes, so don't call this after adding/removing elements. DO call this when you need data to answer questions about utilization, SPOFs, flow paths, or capacity — the data is not available from get_topology_info. Returns flow paths, link utilization (with link IDs), SPOFs, and failure scenarios.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "get_spof_analysis",
        "description": "Get a focused Single Points of Failure (SPOF) analysis. Use this when the user asks about SPOFs, vulnerabilities, or single points of failure — NOT for general simulation. Returns which nodes or links would isolate parts of the network if they failed, with IDs for highlighting.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "run_monte_carlo",
        "description": "Run a time-based Monte Carlo availability simulation. Each iteration simulates 1 year of operation with components failing and being repaired based on MTBF/MTTR exponential distributions. Creates temporary user nodes connected to the selected A-end and Z-end devices to measure end-to-end availability. Returns availability percentage, annual downtime, and standard deviation. By default, planned (not yet built) nodes and links are excluded. Set include_planned=true to include them.",
        "input_schema": {
            "type": "object",
            "properties": {
                "a_location": {"type": "string", "description": "A-end location (node name, node ID, or location string like 'Denver')"},
                "z_location": {"type": "string", "description": "Z-end location (node name, node ID, or location string like 'Dallas')"},
                "a_node_ids": {"type": "array", "items": {"type": "integer"}, "description": "Specific node IDs to connect the temp A-user to. If omitted, connects to all nodes matching a_location."},
                "z_node_ids": {"type": "array", "items": {"type": "integer"}, "description": "Specific node IDs to connect the temp Z-user to. If omitted, connects to all nodes matching z_location."},
                "iterations": {"type": "integer", "description": "Number of iterations — each simulates 1 year (default 1000). Higher = more precise but slower (~2.5s per 1000)."},
                "include_planned": {"type": "boolean", "description": "Include planned (not yet built) nodes and links in the simulation. Default false — only active infrastructure is simulated."},
            },
            "required": ["a_location", "z_location"],
        },
    },
    # --- Report tools ---
    {
        "name": "generate_report",
        "description": "Generate a downloadable CSV report. Available report types: 'simulation' (link utilization, flow paths, SPOFs), 'cost_allocation' (customer cost breakdown with formulas). The report is returned as a download link. For simulation reports, you can optionally include planned infrastructure — planned links/nodes will be marked with a 'PLANNED' status in the report.",
        "input_schema": {
            "type": "object",
            "properties": {
                "report_type": {"type": "string", "enum": ["simulation", "cost_allocation"], "description": "Type of report to generate"},
                "include_planned": {"type": "boolean", "description": "Include planned (not yet built) links and nodes in the simulation report. They will be marked as PLANNED in the Status column. Default: false"},
                "allocation_model": {"type": "string", "enum": ["proportional_used", "proportional_total"], "description": "Cost allocation model (only for cost_allocation report). Default: proportional_used"},
            },
            "required": ["report_type"],
        },
    },
    # --- Toggle control tools ---
    {
        "name": "set_topology_toggles",
        "description": "Control topology view toggles. Use this to show/hide planned infrastructure and include/exclude it from simulations.",
        "input_schema": {
            "type": "object",
            "properties": {
                "hide_planned": {"type": "boolean", "description": "Hide planned nodes/links from the canvas (true=hidden, false=visible)"},
                "include_planned": {"type": "boolean", "description": "Include planned nodes/links in simulation and Monte Carlo (true=included, false=excluded)"},
            },
            "required": [],
        },
    },
    # --- Canvas control tools ---
    {
        "name": "highlight_elements",
        "description": "Highlight multiple nodes and/or links on the topology canvas with a bright amber glow. Use this to visually point out specific elements to the user (e.g. 'these are the overloaded links' or 'these nodes form the critical path'). Highlights clear when the user clicks elsewhere or when you highlight new elements.",
        "input_schema": {
            "type": "object",
            "properties": {
                "node_ids": {"type": "array", "items": {"type": "integer"}, "description": "Node IDs to highlight"},
                "link_ids": {"type": "array", "items": {"type": "integer"}, "description": "Link IDs to highlight"},
                "node_names": {"type": "array", "items": {"type": "string"}, "description": "Node names to highlight (resolved to IDs)"},
            },
            "required": [],
        },
    },
    {
        "name": "set_color_scheme",
        "description": "Switch the topology color scheme to highlight different aspects. Options: 'utilization' (link traffic %), 'cost_per_gb' (circuit cost per Gbps of demand), 'circuit_cost' (monthly circuit cost, expensive=purple, cheap=yellow, no cost=blue), 'latency' (one-way link latency in ms, low=teal, high=red, unset=grey).",
        "input_schema": {
            "type": "object",
            "properties": {
                "scheme": {"type": "string", "enum": ["utilization", "cost_per_gb", "circuit_cost", "latency"], "description": "Color scheme to apply"},
            },
            "required": ["scheme"],
        },
    },
    # --- Navigation tool ---
    {
        "name": "focus_element",
        "description": "Center the topology canvas on a specific node or link. Use this when the user asks you to find, locate, or show them a specific element. The view will smoothly pan and zoom to center on it.",
        "input_schema": {
            "type": "object",
            "properties": {
                "node_id": {"type": "integer", "description": "Node ID to center on"},
                "link_id": {"type": "integer", "description": "Link ID to center on"},
                "node_name": {"type": "string", "description": "Node name to search for (use if you don't know the ID)"},
            },
            "required": [],
        },
    },
    # --- Settings tool ---
    {
        "name": "manage_settings",
        "description": "View or update global settings. Available settings: default_node_mtbf_hours, default_node_mttr_hours, default_link_mtbf_hours, default_link_mttr_hours, monte_carlo_iterations, cost_per_gb_default. Call with just action='list' to see current values, or action='update' with key and value to change a setting.",
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["list", "update"], "description": "'list' to view all settings, 'update' to change one"},
                "key": {"type": "string", "description": "Setting key to update (only for action='update')"},
                "value": {"type": "number", "description": "New value (only for action='update')"},
            },
            "required": ["action"],
        },
    },
    # --- Help / Documentation tool ---
    {
        "name": "get_app_help",
        "description": "Get documentation about how to use a feature in the Phanero app. Use this when the user asks how to do something, where to find a feature, or what a button does. Available topics: overview, nodes, links, flows, simulation, maintenance, customers, cost_analysis, color_schemes, prometheus, discovery, topology_management, keyboard_shortcuts, planned_infrastructure. You can also search by keyword.",
        "input_schema": {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "Documentation topic to look up (e.g. 'nodes', 'simulation', 'cost_analysis'). Use 'search' to search by keyword."},
                "search_query": {"type": "string", "description": "Search keyword (only used when topic='search')"},
            },
            "required": ["topic"],
        },
    },
    # --- Maintenance / What-If tools ---
    {
        "name": "run_what_if",
        "description": "Run a 'what if' / maintenance scenario: simulate what happens if specific nodes and/or links are removed from the network. Shows which flows break, which get rerouted, capacity impact, overloaded links, and isolated nodes. Use this to assess the impact of planned maintenance, outages, or failure scenarios.",
        "input_schema": {
            "type": "object",
            "properties": {
                "failed_node_ids": {"type": "array", "items": {"type": "integer"}, "description": "Node IDs to take out of service"},
                "failed_link_ids": {"type": "array", "items": {"type": "integer"}, "description": "Link IDs to take out of service"},
            },
            "required": [],
        },
    },
    {
        "name": "list_maintenance_presets",
        "description": "List saved maintenance/failure presets for the topology. Each preset is a named set of nodes and links that can be quickly applied as a what-if scenario.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "save_maintenance_preset",
        "description": "Save a maintenance/failure scenario as a named preset for future use.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Name for the preset (e.g. 'Denver outage', 'East coast maintenance')"},
                "node_ids": {"type": "array", "items": {"type": "integer"}, "description": "Node IDs in the failure scenario"},
                "link_ids": {"type": "array", "items": {"type": "integer"}, "description": "Link IDs in the failure scenario"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "clear_maintenance",
        "description": "Restore all nodes and links currently in maintenance back into the topology. Clears the what-if / maintenance scenario and returns the network to its normal operational state.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    # --- Discovery & Prometheus tools ---
    {
        "name": "check_prometheus_status",
        "description": "Check the Prometheus integration status for this topology. Tests connectivity, shows SNMP target health, and identifies what data is available or missing. Use this to troubleshoot discovery or live metrics issues.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "run_network_discovery",
        "description": "Run network discovery from a seed device using IGP (ISIS/OSPF) or LLDP. Discovers the network topology by SSHing into the seed device, then enriches with Prometheus SNMP data (interface speeds, names, IP addresses) if Prometheus is configured. Returns the discovered nodes and links.",
        "input_schema": {
            "type": "object",
            "properties": {
                "seed_ip": {"type": "string", "description": "IP address of the seed device to start discovery from"},
                "os_type": {"type": "string", "enum": ["iosxr", "iosxe", "junos", "nxos"], "description": "Operating system of the seed device"},
                "username": {"type": "string", "description": "SSH username for the seed device"},
                "password": {"type": "string", "description": "SSH password for the seed device"},
                "discovery_method": {"type": "string", "enum": ["igp", "lldp"], "description": "Discovery method: 'igp' (ISIS/OSPF from seed only) or 'lldp' (recursive LLDP/CDP crawl). Default: igp"},
                "igp_protocol": {"type": "string", "enum": ["isis", "ospf"], "description": "IGP protocol to use (only for igp method). Default: isis"},
                "clear_existing": {"type": "boolean", "description": "Clear existing nodes/links before discovery (default true)"},
            },
            "required": ["seed_ip", "os_type", "username", "password"],
        },
    },
    {
        "name": "discover_prometheus_devices",
        "description": "Discover network devices from Prometheus SNMP targets. Shows device hostnames, interface names/speeds, and health status. Requires Prometheus to be configured for this topology first.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "diagnose_prometheus_data",
        "description": "Deep diagnostic of what Prometheus data is available for the topology's devices. Checks sysName, interface names, speeds, LLDP neighbors, IP addresses, and traffic counters for each SNMP target. Identifies specific gaps that would prevent discovery enrichment or live metrics from working. Use this when troubleshooting data issues.",
        "input_schema": {
            "type": "object",
            "properties": {
                "device_ip": {"type": "string", "description": "Optional: check a specific device IP. If omitted, checks all SNMP targets."},
            },
            "required": [],
        },
    },
    {
        "name": "configure_prometheus",
        "description": "Configure or update the Prometheus connection for this topology. Set the URL, authentication, and metric configuration.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Prometheus URL (e.g. 'http://prometheus:9090')"},
                "auth_type": {"type": "string", "enum": ["none", "basic", "bearer"], "description": "Authentication type (default: none)"},
                "username": {"type": "string", "description": "Username for basic auth"},
                "password": {"type": "string", "description": "Password for basic auth"},
                "bearer_token": {"type": "string", "description": "Token for bearer auth"},
                "enabled": {"type": "boolean", "description": "Enable/disable live metrics"},
            },
            "required": ["url"],
        },
    },
    {
        "name": "enrich_from_prometheus",
        "description": "Enrich the existing topology with SNMP data from Prometheus WITHOUT re-running discovery. Auto-matches nodes to SNMP targets by hostname (sets prometheus_id), updates interface names and speeds from SNMP, fills missing link interfaces via LLDP and IP subnet correlation, and populates source/target IPs on links. Use this when the topology already has nodes and links but needs Prometheus data applied to them.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "get_live_metrics",
        "description": "Get current live utilization metrics from Prometheus for all links in the topology. Requires Prometheus to be configured and enabled, and nodes to have prometheus_id set. Returns per-link utilization percentages.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
]


def _satellite_rule_error(src: NodeInstance, tgt: NodeInstance) -> str | None:
    """The assistant only makes terrestrial links; satellite (dish ↔ PoP)
    links need an orbit and are created in the Links panel."""
    from app.services.satellite import validate_link_roles, SatelliteLinkError
    try:
        validate_link_roles(src.role, tgt.role, "terrestrial", "")
    except SatelliteLinkError as e:
        return f"{e}. Satellite links (dish ↔ PoP) are created in the Links panel, where you choose the provider and orbit."
    return None


def _get_topology_info(topology_id: int, db: Session) -> dict:
    topo = db.query(Topology).filter(Topology.id == topology_id).first()
    nodes = db.query(NodeInstance).filter(NodeInstance.topology_id == topology_id).all()
    links = db.query(Link).filter(Link.topology_id == topology_id).all()
    flows = db.query(Flow).filter(Flow.topology_id == topology_id).all()
    templates = db.query(NodeTemplate).all()
    lat = latency_infos(db, nodes, links)

    # Build used-interfaces map per node
    used_ifaces: dict[int, set[str]] = {}
    for l in links:
        used_ifaces.setdefault(l.source_node_id, set()).add(l.source_interface)
        used_ifaces.setdefault(l.target_node_id, set()).add(l.target_interface)
    node_customer = node_customer_map(nodes)

    return {
        "topology": {"id": topo.id, "name": topo.name, "description": topo.description} if topo else None,
        "nodes": [
            {"id": n.id, "name": n.name, "vendor": n.vendor, "model": n.model,
             "platform": n.platform, "capacity_gbps": n.capacity_gbps,
             "mtbf_hours": n.mtbf_hours, "mttr_hours": n.mttr_hours,
             "location": n.location or "",
             "prometheus_id": n.prometheus_id or "", "mgmt_ip": n.mgmt_ip or "",
             "is_planned": bool(n.is_planned),
             "role": n.role or "router",
             "customer_id": n.customer_id,
             "interfaces": [
                 {**iface, "in_use": iface["name"] in used_ifaces.get(n.id, set())}
                 for iface in n.interfaces
             ],
             "x": n.x, "y": n.y}
            for n in nodes
        ],
        "links": [
            {"id": l.id, "source_node_id": l.source_node_id, "target_node_id": l.target_node_id,
             "source_node": next((n.name for n in nodes if n.id == l.source_node_id), "?"),
             "target_node": next((n.name for n in nodes if n.id == l.target_node_id), "?"),
             "source_interface": l.source_interface, "target_interface": l.target_interface,
             "bandwidth_gbps": l.bandwidth_gbps, "cost": l.cost,
             # Effective latency: measured, estimated (from the map) or unknown (None).
             "latency_ms": lat[l.id].ms, "latency_source": lat[l.id].source,
             "carrier": l.carrier, "mtbf_hours": l.mtbf_hours, "mttr_hours": l.mttr_hours,
             "circuit_cost": l.circuit_cost or 0, "circuit_id": l.circuit_id or "",
             "link_id_label": l.link_id_label or "",
             "source_ip": l.source_ip or "", "target_ip": l.target_ip or "",
             "is_planned": bool(l.is_planned)}
            for l in links
        ],
        "flows": [
            {"id": f.id, "name": f.name, "description": f.description or "",
             "source_node_id": f.source_node_id, "dest_node_id": f.dest_node_id,
             "source_node": next((n.name for n in nodes if n.id == f.source_node_id), "?"),
             "dest_node": next((n.name for n in nodes if n.id == f.dest_node_id), "?"),
             "bandwidth_gbps": f.bandwidth_gbps, "routing_behavior": f.routing_behavior,
             "ecmp_enabled": bool(f.ecmp_enabled),
             "customer_ids": flow_customer_ids(f, node_customer)}
            for f in flows
        ],
        "available_templates": [
            {"id": t.id, "name": t.name, "vendor": t.vendor, "model": t.model,
             "platform": t.platform, "capacity_gbps": t.capacity_gbps,
             "role": t.role or "router",
             "interfaces_count": len(t.default_interfaces)}
            for t in templates
        ],
    }


def _execute_tool(tool_name: str, tool_input: dict, topology_id: int, db: Session) -> tuple[str, str, dict | None]:
    """Execute a tool and return (result_text, action_description, extra_data)."""
    if tool_name == "get_topology_info":
        info = _get_topology_info(topology_id, db)
        return json.dumps(info, indent=2), "", None

    elif tool_name == "add_node":
        existing = db.query(NodeInstance).filter(NodeInstance.topology_id == topology_id, NodeInstance.name == tool_input["name"]).first()
        if existing:
            return json.dumps({"error": f"A node named '{tool_input['name']}' already exists (id={existing.id}). Use a different name."}), "", None
        node = NodeInstance(
            topology_id=topology_id,
            template_id=tool_input.get("template_id"),
            name=tool_input["name"],
            vendor=tool_input.get("vendor", ""),
            model=tool_input.get("model", ""),
            platform=tool_input.get("platform", ""),
            x=tool_input.get("x", 0),
            y=tool_input.get("y", 0),
            latitude=tool_input.get("latitude"),
            longitude=tool_input.get("longitude"),
            mtbf_hours=tool_input.get("mtbf_hours"),
            mttr_hours=tool_input.get("mttr_hours"),
            capacity_gbps=tool_input.get("capacity_gbps", 100),
            location=tool_input.get("location", ""),
            is_planned=1 if tool_input.get("is_planned") else 0,
            role=tool_input.get("role", "router"),
            customer_id=tool_input.get("customer_id"),
        )
        # Customer nodes default to MTTR=0 (don't impact availability)
        if node.role == "customer" and node.mttr_hours is None:
            node.mttr_hours = 0
        # If template specified, inherit properties
        if node.template_id:
            tmpl = db.query(NodeTemplate).filter(NodeTemplate.id == node.template_id).first()
            if tmpl:
                if not node.vendor:
                    node.vendor = tmpl.vendor
                if not node.model:
                    node.model = tmpl.model
                if not node.platform:
                    node.platform = tmpl.platform
                if node.mtbf_hours is None:
                    node.mtbf_hours = tmpl.default_mtbf_hours
                if node.mttr_hours is None:
                    node.mttr_hours = tmpl.default_mttr_hours
                node.capacity_gbps = tmpl.capacity_gbps
                node.interfaces_json = tmpl.default_interfaces_json
                if node.role == "router" and tmpl.role:
                    node.role = tmpl.role
        # Compute x/y from lat/lng if provided
        if node.latitude is not None and node.longitude is not None:
            from app.routes.nodes import lat_lng_to_xy
            node.x, node.y = lat_lng_to_xy(node.latitude, node.longitude)
        db.add(node)
        db.commit()
        db.refresh(node)
        return json.dumps({"id": node.id, "name": node.name, "vendor": node.vendor, "model": node.model}), f"Added node '{node.name}'", None

    elif tool_name == "remove_node":
        node = db.query(NodeInstance).filter(NodeInstance.id == tool_input["node_id"], NodeInstance.topology_id == topology_id).first()
        if not node:
            return "Node not found", "", None
        name = node.name
        # Remove connected links
        link_q = db.query(Link).filter(
            Link.topology_id == topology_id,
            (Link.source_node_id == node.id) | (Link.target_node_id == node.id)
        )
        from app.routes.patch_panels import strip_patch_panel_references
        strip_patch_panel_references(db, node_ids=[node.id], link_ids=[l.id for l in link_q.all()])
        link_q.delete(synchronize_session=False)
        # Remove flows referencing this node
        db.query(Flow).filter(
            Flow.topology_id == topology_id,
            (Flow.source_node_id == node.id) | (Flow.dest_node_id == node.id)
        ).delete(synchronize_session=False)
        db.delete(node)
        db.commit()
        return f"Removed node '{name}' and its connected links/flows", f"Removed node '{name}'", None

    elif tool_name == "add_link":
        from app.routes.links import _validate_link_interfaces, _get_used_interfaces

        src_node = db.query(NodeInstance).filter(NodeInstance.id == tool_input["source_node_id"], NodeInstance.topology_id == topology_id).first()
        tgt_node = db.query(NodeInstance).filter(NodeInstance.id == tool_input["target_node_id"], NodeInstance.topology_id == topology_id).first()
        if not src_node:
            return f"Source node {tool_input['source_node_id']} not found", "", None
        if not tgt_node:
            return f"Target node {tool_input['target_node_id']} not found", "", None
        sat_err = _satellite_rule_error(src_node, tgt_node)
        if sat_err:
            return json.dumps({"error": sat_err}), "", None

        src_iface = tool_input.get("source_interface", "")
        tgt_iface = tool_input.get("target_interface", "")
        if not src_iface or not tgt_iface:
            # Help the AI by listing available interfaces
            src_used = _get_used_interfaces(topology_id, src_node.id, db)
            tgt_used = _get_used_interfaces(topology_id, tgt_node.id, db)
            src_avail = [i for i in src_node.interfaces if i["name"] not in src_used]
            tgt_avail = [i for i in tgt_node.interfaces if i["name"] not in tgt_used]
            return json.dumps({
                "error": "source_interface and target_interface are required. Interfaces must have matching speeds.",
                "source_node": src_node.name,
                "source_available": [{"name": i["name"], "speed_gbps": i["speed_gbps"]} for i in src_avail],
                "target_node": tgt_node.name,
                "target_available": [{"name": i["name"], "speed_gbps": i["speed_gbps"]} for i in tgt_avail],
            }, indent=2), "", None

        # Validate interfaces
        class TempData:
            pass
        temp = TempData()
        temp.source_interface = src_iface
        temp.target_interface = tgt_iface
        try:
            _, _, speed = _validate_link_interfaces(temp, topology_id, db, src_node, tgt_node)
        except Exception as e:
            return f"Validation error: {e.detail if hasattr(e, 'detail') else str(e)}", "", None

        from app.routes.links import REFERENCE_BANDWIDTH_GBPS
        auto_cost = REFERENCE_BANDWIDTH_GBPS / speed
        link = Link(
            topology_id=topology_id,
            source_node_id=tool_input["source_node_id"],
            target_node_id=tool_input["target_node_id"],
            bandwidth_gbps=speed,
            cost=tool_input.get("cost", auto_cost),
            latency_ms=tool_input.get("latency_ms") or 0.0,
            latency_source=infer_latency_source(tool_input.get("latency_ms")),
            carrier=tool_input.get("carrier", ""),
            source_interface=src_iface,
            target_interface=tgt_iface,
            mtbf_hours=tool_input.get("mtbf_hours"),
            mttr_hours=tool_input.get("mttr_hours"),
            circuit_cost=tool_input.get("circuit_cost", 0),
            circuit_id=tool_input.get("circuit_id", ""),
            link_id_label=tool_input.get("link_id_label", ""),
            is_planned=1 if tool_input.get("is_planned") else 0,
        )
        db.add(link)
        db.commit()
        db.refresh(link)
        return json.dumps({
            "id": link.id, "source": src_node.name, "target": tgt_node.name,
            "source_interface": src_iface, "target_interface": tgt_iface,
            "bandwidth_gbps": link.bandwidth_gbps,
        }), f"Added {link.bandwidth_gbps}G link {src_node.name}:{src_iface} <-> {tgt_node.name}:{tgt_iface}", None

    elif tool_name == "remove_link":
        link = db.query(Link).filter(Link.id == tool_input["link_id"], Link.topology_id == topology_id).first()
        if not link:
            return "Link not found", "", None
        desc = f"Removed link #{link.id}"
        from app.routes.patch_panels import strip_patch_panel_references
        strip_patch_panel_references(db, link_ids=[link.id])
        db.delete(link)
        db.commit()
        return desc, desc, None

    elif tool_name == "add_flow":
        flow = Flow(
            topology_id=topology_id,
            name=tool_input["name"],
            source_node_id=tool_input["source_node_id"],
            dest_node_id=tool_input["dest_node_id"],
            bandwidth_gbps=tool_input.get("bandwidth_gbps", 1),
            routing_behavior=tool_input.get("routing_behavior", "shortest_path"),
            ecmp_enabled=1 if tool_input.get("ecmp_enabled", False) else 0,
            explicit_path_json=json.dumps(tool_input.get("explicit_path", [])),
        )
        db.add(flow)
        db.commit()
        db.refresh(flow)
        src = db.query(NodeInstance).filter(NodeInstance.id == flow.source_node_id).first()
        dst = db.query(NodeInstance).filter(NodeInstance.id == flow.dest_node_id).first()
        return json.dumps({"id": flow.id, "name": flow.name, "source": src.name if src else "?", "dest": dst.name if dst else "?"}), f"Added flow '{flow.name}'", None

    elif tool_name == "update_flow":
        flow = db.query(Flow).filter(Flow.id == tool_input["flow_id"], Flow.topology_id == topology_id).first()
        if not flow:
            return "Flow not found", "", None
        for field in ["name", "routing_behavior", "bandwidth_gbps", "source_node_id", "dest_node_id"]:
            if field in tool_input and tool_input[field] is not None:
                setattr(flow, field, tool_input[field])
        if "ecmp_enabled" in tool_input:
            flow.ecmp_enabled = 1 if tool_input["ecmp_enabled"] else 0
        db.commit()
        node_map = {n.id: n.name for n in db.query(NodeInstance).filter(NodeInstance.topology_id == topology_id).all()}
        return json.dumps({
            "id": flow.id, "name": flow.name,
            "routing_behavior": flow.routing_behavior,
            "source": node_map.get(flow.source_node_id, "?"),
            "dest": node_map.get(flow.dest_node_id, "?"),
            "bandwidth_gbps": flow.bandwidth_gbps,
            "ecmp_enabled": bool(flow.ecmp_enabled),
        }), f"Updated flow '{flow.name}'", None

    elif tool_name == "bulk_update_flows":
        all_flows = db.query(Flow).filter(Flow.topology_id == topology_id).all()
        node_map = {n.id: n.name for n in db.query(NodeInstance).filter(NodeInstance.topology_id == topology_id).all()}

        # Apply filters
        matched = all_flows
        if "filter_source_node_id" in tool_input:
            matched = [f for f in matched if f.source_node_id == tool_input["filter_source_node_id"]]
        if "filter_dest_node_id" in tool_input:
            matched = [f for f in matched if f.dest_node_id == tool_input["filter_dest_node_id"]]
        if "filter_source_or_dest_node_id" in tool_input:
            nid = tool_input["filter_source_or_dest_node_id"]
            matched = [f for f in matched if f.source_node_id == nid or f.dest_node_id == nid]
        if "filter_customer_id" in tool_input:
            # Chain-wide: a child's flows may terminate on inherited customer nodes.
            from app.services.topology_chain import get_topology_chain
            chain = get_topology_chain(db, topology_id)
            node_customer = node_customer_map(db.query(NodeInstance).filter(NodeInstance.topology_id.in_(chain)).all())
            matched = [f for f in matched if tool_input["filter_customer_id"] in flow_customer_ids(f, node_customer)]
        if "filter_routing_behavior" in tool_input:
            matched = [f for f in matched if f.routing_behavior == tool_input["filter_routing_behavior"]]

        if not matched:
            return json.dumps({"error": "No flows matched the filter criteria", "total_flows": len(all_flows)}), "", None

        # Apply updates
        updated_count = 0
        for flow in matched:
            changed = False
            if "set_routing_behavior" in tool_input:
                flow.routing_behavior = tool_input["set_routing_behavior"]
                changed = True
            if "set_ecmp_enabled" in tool_input:
                flow.ecmp_enabled = 1 if tool_input["set_ecmp_enabled"] else 0
                changed = True
            if "set_bandwidth_gbps" in tool_input:
                flow.bandwidth_gbps = tool_input["set_bandwidth_gbps"]
                changed = True
            if changed:
                updated_count += 1

        db.commit()
        flow_names = [f.name for f in matched]
        return json.dumps({
            "matched": len(matched),
            "updated": updated_count,
            "flow_names": flow_names,
        }, indent=2), f"Updated {updated_count} flow(s)", None

    elif tool_name == "remove_flow":
        flow = db.query(Flow).filter(Flow.id == tool_input["flow_id"], Flow.topology_id == topology_id).first()
        if not flow:
            return "Flow not found", "", None
        name = flow.name
        db.delete(flow)
        db.commit()
        return f"Removed flow '{name}'", f"Removed flow '{name}'", None

    elif tool_name == "update_link":
        link = db.query(Link).filter(Link.id == tool_input["link_id"], Link.topology_id == topology_id).first()
        if not link:
            return "Link not found", "", None
        # cost is excluded — agent must not modify routing cost; use update_flow routing_behavior instead
        tool_input.pop("cost", None)
        # Handle endpoint changes (node or interface) with validation
        endpoint_keys = ("source_node_id", "target_node_id", "source_interface", "target_interface")
        if any(k in tool_input for k in endpoint_keys):
            from app.routes.links import _validate_link_interfaces, REFERENCE_BANDWIDTH_GBPS
            src_node_id = tool_input.get("source_node_id", link.source_node_id)
            tgt_node_id = tool_input.get("target_node_id", link.target_node_id)
            src_iface = tool_input.get("source_interface", link.source_interface)
            tgt_iface = tool_input.get("target_interface", link.target_interface)
            if src_node_id == tgt_node_id:
                return json.dumps({"error": "Source and target nodes must be different"}), "", None
            source_node = db.query(NodeInstance).filter(NodeInstance.id == src_node_id, NodeInstance.topology_id == topology_id).first()
            target_node = db.query(NodeInstance).filter(NodeInstance.id == tgt_node_id, NodeInstance.topology_id == topology_id).first()
            if not source_node or not target_node:
                return json.dumps({"error": "Source or target node not found"}), "", None
            sat_err = _satellite_rule_error(source_node, target_node)
            if sat_err:
                return json.dumps({"error": sat_err}), "", None
            class TempData: pass
            temp = TempData()
            temp.source_interface = src_iface
            temp.target_interface = tgt_iface
            try:
                _, _, speed = _validate_link_interfaces(temp, topology_id, db, source_node, target_node, exclude_link_id=link.id)
            except HTTPException as e:
                return json.dumps({"error": e.detail}), "", None
            link.source_node_id = src_node_id
            link.target_node_id = tgt_node_id
            link.source_interface = src_iface
            link.target_interface = tgt_iface
            link.bandwidth_gbps = speed
            link.cost = REFERENCE_BANDWIDTH_GBPS / speed
            # The assistant only makes terrestrial links (the pair was checked above).
            link.transport, link.orbit = "terrestrial", ""
        for field in ["bandwidth_gbps", "latency_ms", "carrier", "mtbf_hours", "mttr_hours",
                       "circuit_cost", "circuit_id", "link_id_label", "source_ip", "target_ip"]:
            if field in tool_input:
                setattr(link, field, tool_input[field])
        if "latency_ms" in tool_input:
            # Setting a latency makes it known (0 / empty = unknown, estimated).
            link.latency_source = "measured" if (tool_input["latency_ms"] or 0) > 0 else "unknown"
        if "is_planned" in tool_input:
            link.is_planned = 1 if tool_input["is_planned"] else 0
        if "color" in tool_input:
            link.color = tool_input["color"]
        db.commit()
        return f"Updated link #{link.id}", f"Updated link #{link.id}", None

    elif tool_name == "bulk_update_links":
        link_ids = tool_input.get("link_ids", [])
        if not link_ids:
            return json.dumps({"error": "link_ids is required"}), "", None
        links_to_update = db.query(Link).filter(Link.topology_id == topology_id, Link.id.in_(link_ids)).all()
        if not links_to_update:
            return json.dumps({"error": "No matching links found"}), "", None
        for link in links_to_update:
            if "color" in tool_input:
                link.color = tool_input["color"]
            if "carrier" in tool_input:
                link.carrier = tool_input["carrier"]
            if "circuit_cost" in tool_input:
                link.circuit_cost = tool_input["circuit_cost"]
        db.commit()
        return json.dumps({"updated": len(links_to_update), "link_ids": [l.id for l in links_to_update]}), f"Updated {len(links_to_update)} link(s)", None

    elif tool_name == "update_node":
        node = db.query(NodeInstance).filter(NodeInstance.id == tool_input["node_id"], NodeInstance.topology_id == topology_id).first()
        if not node:
            return "Node not found", "", None
        if "name" in tool_input and tool_input["name"] != node.name:
            conflict = db.query(NodeInstance).filter(NodeInstance.topology_id == topology_id, NodeInstance.name == tool_input["name"], NodeInstance.id != node.id).first()
            if conflict:
                return json.dumps({"error": f"A node named '{tool_input['name']}' already exists (id={conflict.id}). Use a different name."}), "", None
        for field in ["name", "vendor", "model", "platform", "location", "mtbf_hours", "mttr_hours",
                       "capacity_gbps", "prometheus_id", "mgmt_ip"]:
            if field in tool_input:
                setattr(node, field, tool_input[field])
        if "is_planned" in tool_input:
            node.is_planned = 1 if tool_input["is_planned"] else 0
        if "role" in tool_input:
            node.role = tool_input["role"]
        if "customer_id" in tool_input:
            node.customer_id = tool_input["customer_id"] if tool_input["customer_id"] else None
        db.commit()
        return f"Updated node '{node.name}'", f"Updated node '{node.name}'", None

    elif tool_name == "run_simulation":
        result = run_simulation(topology_id, db)
        summary_parts = []
        summary_parts.append(f"Routed {len(result['flow_paths'])} flows")
        summary_parts.append(f"{len(result['single_points_of_failure'])} SPOFs found")
        if result["link_utilizations"]:
            max_util = max(lu["utilization_pct"] for lu in result["link_utilizations"])
            summary_parts.append(f"Max link utilization: {max_util}%")
        summary = ". ".join(summary_parts)

        # Include detailed results
        detail = {"summary": summary, "flow_paths": [], "link_utilizations": [], "spofs": []}
        nodes_map = {}
        for n in db.query(NodeInstance).filter(NodeInstance.topology_id == topology_id).all():
            nodes_map[n.id] = n.name

        for fp in result["flow_paths"]:
            paths_readable = []
            for p in fp["paths"]:
                paths_readable.append(" -> ".join(nodes_map.get(nid, str(nid)) for nid in p))
            detail["flow_paths"].append({"flow": fp["flow_name"], "paths": paths_readable, "bw_per_path": fp["bandwidth_per_path_gbps"]})

        for lu in sorted(result["link_utilizations"], key=lambda x: x["utilization_pct"]):
            detail["link_utilizations"].append({
                "link_id": lu["link_id"],
                "link": f"{nodes_map.get(lu['source_node_id'], '?')} <-> {nodes_map.get(lu['target_node_id'], '?')}",
                "used": f"{lu['used_gbps']}G / {lu['bandwidth_gbps']}G",
                "utilization": f"{lu['utilization_pct']}%",
                "cost_per_gb": f"${lu['cost_per_gb']}",
            })

        for spof in result["single_points_of_failure"]:
            detail["spofs"].append({
                "type": spof["type"], "id": spof["id"], "name": spof["name"],
                "affected_flows": spof["affected_flows"],
                "isolated_count": spof.get("isolated_count", 0),
                "isolated_node_ids": spof.get("isolated_node_ids", []),
            })

        return json.dumps(detail, indent=2), "Ran simulation", {"simulation_result": result}

    elif tool_name == "get_spof_analysis":
        result = run_simulation(topology_id, db)
        nodes_map = {}
        for n in db.query(NodeInstance).filter(NodeInstance.topology_id == topology_id).all():
            nodes_map[n.id] = n.name

        spofs = result["single_points_of_failure"]
        if not spofs:
            return "No single points of failure detected. No single node or link failure would isolate any part of the network.", "SPOF analysis complete — no SPOFs found", {"simulation_result": result}

        detail = {"spof_count": len(spofs), "spofs": []}
        for spof in spofs:
            isolated_names = [nodes_map.get(nid, str(nid)) for nid in spof.get("isolated_node_ids", [])]
            detail["spofs"].append({
                "type": spof["type"],
                "id": spof["id"],
                "name": spof["name"],
                "affected_flows": spof["affected_flows"],
                "isolated_nodes": isolated_names,
                "isolated_count": spof.get("isolated_count", 0),
            })

        # Also include failure scenarios with isolation details
        detail["failure_scenarios_with_isolation"] = []
        for fs in result["failure_scenarios"]:
            if fs.get("isolated_node_ids"):
                detail["failure_scenarios_with_isolation"].append({
                    "failed_element": f"{fs['failed_element_type']}: {nodes_map.get(fs['failed_element_id'], fs['failed_element_id'])}",
                    "is_spof": fs["spof"],
                    "isolated_nodes": fs.get("isolated_node_names", []),
                    "affected_flows": len(fs["affected_flows"]),
                })

        summary = f"Found {len(spofs)} SPOF(s): " + ", ".join(
            f"{s['type']} '{s['name']}' (isolates {s.get('isolated_count', 0)} node(s))" for s in spofs
        )
        return json.dumps(detail, indent=2), summary, {"simulation_result": result}

    elif tool_name == "run_monte_carlo":
        a_loc = tool_input.get("a_location", "")
        z_loc = tool_input.get("z_location", "")
        if not a_loc or not z_loc:
            return "Both a_location and z_location are required", "", None
        iterations = tool_input.get("iterations", 1000)
        a_node_ids = tool_input.get("a_node_ids")
        z_node_ids = tool_input.get("z_node_ids")
        include_planned = tool_input.get("include_planned", False)
        result = run_monte_carlo(topology_id, db, a_loc, z_loc,
                                 a_node_ids=a_node_ids, z_node_ids=z_node_ids,
                                 iterations=iterations, include_planned=include_planned)
        if result.get("error"):
            return f"Error: {result['error']}", "", None

        a_devs = ", ".join(d["name"] for d in result.get("a_devices", []))
        z_devs = ", ".join(d["name"] for d in result.get("z_devices", []))
        summary = (
            f"Monte Carlo ({iterations} iterations, each simulating 1 year):\n"
            f"A-end: {a_devs} | Z-end: {z_devs}\n"
            f"Availability: {result['availability'] * 100:.4f}%\n"
            f"Downtime: {result['downtime_min_yr']} min/yr\n"
            f"Std dev: {result['std_dev'] * 100:.4f}%\n"
            f"Disjoint paths: {result['disjoint_paths']}\n"
            f"Outage iterations: {result['outage_iterations']}/{iterations}"
        )
        detail = {
            "summary": summary,
            "availability": result["availability"],
            "downtime_min_yr": result["downtime_min_yr"],
            "std_dev": result["std_dev"],
            "downtime_std_min_yr": result["downtime_std_min_yr"],
            "disjoint_paths": result["disjoint_paths"],
            "outage_iterations": result["outage_iterations"],
            "a_devices": result.get("a_devices", []),
            "z_devices": result.get("z_devices", []),
        }
        return json.dumps(detail, indent=2), "Ran Monte Carlo simulation", {"monte_carlo_result": result}

    if tool_name == "generate_report":
        report_type = tool_input.get("report_type", "")
        include_planned = tool_input.get("include_planned", False)
        if report_type == "simulation":
            label = "simulation report (with planned infrastructure)" if include_planned else "simulation report"
            return json.dumps({
                "report_ready": True,
                "download_url": f"/api/topologies/{topology_id}/simulate/export-csv",
                "method": "POST",
                "message": f"Simulation report is ready. The user can download it from the Simulation panel (Utilization tab → 'Export Simulation Report' button), or use the download link.",
            }), f"Generated {label}", {"report_download": {"type": "simulation", "topology_id": topology_id, "include_planned": include_planned}}
        elif report_type == "cost_allocation":
            model = tool_input.get("allocation_model", "proportional_used")
            from app.services.cost_allocation import run_cost_allocation
            result = run_cost_allocation(topology_id, db, model)
            # Build CSV
            lines = []
            lines.append("=== CUSTOMER COST SUMMARY ===")
            lines.append("Customer,Total Cost,Link Count")
            for c in result.get("customers", []):
                lines.append(f'"{c["customer_name"]}","${c["total_cost"]:.2f}",{c["link_count"]}')
            lines.append("")
            lines.append("=== CUSTOMER DETAIL (PER LINK) ===")
            lines.append("Customer,Link,Bandwidth (Gbps),Share (%),Allocated Cost")
            for c in result.get("customers", []):
                for l in c.get("links", []):
                    lines.append(f'"{c["customer_name"]}","{l["link_name"]}",{l["bandwidth_gbps"]},{l["share_pct"]}%,"${l["allocated_cost"]:.2f}"')
            lines.append("")
            lines.append("=== LINK BREAKDOWN ===")
            lines.append("Link,Circuit Cost,Capacity (Gbps),Used (Gbps),Utilization (%),Unallocated Cost")
            for lb in result.get("link_breakdowns", []):
                lines.append(f'"{lb["link_name"]}","${lb["circuit_cost"]:.2f}",{lb["bandwidth_gbps"]},{lb["used_gbps"]},{lb["utilization_pct"]}%,"${lb["unallocated_cost"]:.2f}"')
            lines.append("")
            lines.append("=== LINK ALLOCATION DETAIL ===")
            lines.append("Link,Customer,Bandwidth (Gbps),Share (%),Allocated Cost,Formula")
            for lb in result.get("link_breakdowns", []):
                for a in lb.get("allocations", []):
                    lines.append(f'"{lb["link_name"]}","{a["customer_name"]}",{a["bandwidth_gbps"]},{a["share_pct"]}%,"${a["allocated_cost"]:.2f}","{a["formula"]}"')
            csv_content = "\n".join(lines)
            return json.dumps({
                "report_ready": True,
                "message": f"Cost allocation report generated ({model}). Summary: ${result['summary']['total_allocated_cost']:.2f} allocated across {result['summary']['customer_count']} customers.",
                "summary": result["summary"],
            }), "Generated cost allocation report", {"report_download": {"type": "cost_allocation", "topology_id": topology_id, "csv": csv_content}}
        return json.dumps({"error": f"Unknown report type: {report_type}"}), "", None

    if tool_name == "set_topology_toggles":
        toggles = {}
        if "hide_planned" in tool_input:
            toggles["hide_planned"] = tool_input["hide_planned"]
        if "include_planned" in tool_input:
            toggles["include_planned"] = tool_input["include_planned"]
        if not toggles:
            return json.dumps({"error": "Specify at least one toggle"}), "", None
        msgs = []
        if "hide_planned" in toggles:
            msgs.append(f"Hide Planned: {'on' if toggles['hide_planned'] else 'off'}")
        if "include_planned" in toggles:
            msgs.append(f"Include Planned: {'on' if toggles['include_planned'] else 'off'}")
        return json.dumps({"applied": toggles}), ", ".join(msgs), {"set_toggles": toggles}

    if tool_name == "highlight_elements":
        node_ids = list(tool_input.get("node_ids", []))
        link_ids = list(tool_input.get("link_ids", []))
        node_names = tool_input.get("node_names", [])
        # Resolve node names to IDs
        if node_names:
            all_nodes = db.query(NodeInstance).filter(NodeInstance.topology_id == topology_id).all()
            name_map = {n.name.lower(): n.id for n in all_nodes}
            for name in node_names:
                nid = name_map.get(name.lower())
                if nid and nid not in node_ids:
                    node_ids.append(nid)
        count = len(node_ids) + len(link_ids)
        return json.dumps({"highlighted": count, "node_ids": node_ids, "link_ids": link_ids}), \
            f"Highlighted {count} element(s)", {"highlight_nodes": node_ids, "highlight_links": link_ids}

    if tool_name == "set_color_scheme":
        scheme = tool_input.get("scheme", "utilization")
        valid = ["utilization", "cost_per_gb", "circuit_cost", "latency"]
        if scheme not in valid:
            return json.dumps({"error": f"Invalid scheme. Options: {valid}"}), "", None
        labels = {"utilization": "Utilization", "cost_per_gb": "Cost/GB", "circuit_cost": "Circuit Cost", "latency": "Latency"}
        return json.dumps({"applied": True, "scheme": scheme}), f"Switched to {labels[scheme]} view", {"set_color_scheme": scheme}

    if tool_name == "focus_element":
        node_id = tool_input.get("node_id")
        link_id = tool_input.get("link_id")
        node_name = tool_input.get("node_name", "")

        if node_name and not node_id:
            # Search by name
            name_lower = node_name.lower()
            all_nodes = db.query(NodeInstance).filter(NodeInstance.topology_id == topology_id).all()
            matches = [n for n in all_nodes if n.name.lower() == name_lower]
            if not matches:
                matches = [n for n in all_nodes if name_lower in n.name.lower()]
            if matches:
                node_id = matches[0].id
                return json.dumps({"found": True, "node_id": node_id, "name": matches[0].name}), f"Centered on {matches[0].name}", {"focus_node_id": node_id}
            else:
                return json.dumps({"found": False, "message": f"No node matching '{node_name}' found"}), "", None

        if node_id:
            node = db.query(NodeInstance).filter(NodeInstance.id == node_id, NodeInstance.topology_id == topology_id).first()
            if node:
                return json.dumps({"found": True, "node_id": node_id, "name": node.name}), f"Centered on {node.name}", {"focus_node_id": node_id}
            return json.dumps({"found": False, "message": f"Node #{node_id} not found"}), "", None

        if link_id:
            link = db.query(Link).filter(Link.id == link_id, Link.topology_id == topology_id).first()
            if link:
                node_names = {n.id: n.name for n in db.query(NodeInstance).filter(NodeInstance.topology_id == topology_id).all()}
                name = f"{node_names.get(link.source_node_id, '?')} \u2194 {node_names.get(link.target_node_id, '?')}"
                return json.dumps({"found": True, "link_id": link_id, "name": name}), f"Centered on link {name}", {"focus_link_id": link_id}
            return json.dumps({"found": False, "message": f"Link #{link_id} not found"}), "", None

        return json.dumps({"error": "Provide node_id, link_id, or node_name"}), "", None

    if tool_name == "manage_settings":
        from app.models.global_settings import GlobalSettings
        action = tool_input.get("action", "list")
        if action == "list":
            settings = db.query(GlobalSettings).all()
            return json.dumps([{"key": s.key, "value": s.value, "description": s.description} for s in settings], indent=2), "", None
        elif action == "update":
            key = tool_input.get("key", "")
            value = tool_input.get("value")
            if not key or value is None:
                return json.dumps({"error": "key and value are required for update"}), "", None
            setting = db.query(GlobalSettings).filter(GlobalSettings.key == key).first()
            if not setting:
                available = [s.key for s in db.query(GlobalSettings).all()]
                return json.dumps({"error": f"Setting '{key}' not found", "available_keys": available}), "", None
            old_value = setting.value
            setting.value = float(value)
            db.commit()
            return json.dumps({"success": True, "key": key, "old_value": old_value, "new_value": float(value), "description": setting.description}), f"Updated {key}: {old_value} → {value}", None
        return json.dumps({"error": f"Unknown action '{action}'"}), "", None

    if tool_name == "get_app_help":
        from app.services.app_docs import get_doc, get_doc_topics, search_docs
        topic = tool_input.get("topic", "")
        if topic == "search":
            query = tool_input.get("search_query", "")
            if not query:
                return json.dumps({"error": "search_query is required when topic='search'"}), "", None
            results = search_docs(query)
            if not results:
                return json.dumps({"message": f"No documentation found for '{query}'", "available_topics": get_doc_topics()}), "", None
            return json.dumps({"results": results}, indent=2), "", None
        doc = get_doc(topic)
        if not doc:
            return json.dumps({"error": f"Unknown topic '{topic}'", "available_topics": get_doc_topics()}), "", None
        return json.dumps(doc, indent=2), "", None

    if tool_name == "run_what_if":
        from app.services.simulation import run_maintenance_window
        failed_node_ids = tool_input.get("failed_node_ids", [])
        failed_link_ids = tool_input.get("failed_link_ids", [])
        if not failed_node_ids and not failed_link_ids:
            return json.dumps({"error": "Specify at least one node or link to take out of service"}), "", None

        node_name_map = {n.id: n.name for n in db.query(NodeInstance).filter(NodeInstance.topology_id == topology_id).all()}
        link_name_map = {}
        for l in db.query(Link).filter(Link.topology_id == topology_id).all():
            link_name_map[l.id] = f"{node_name_map.get(l.source_node_id, '?')} \u2194 {node_name_map.get(l.target_node_id, '?')}"

        try:
            result = run_maintenance_window(topology_id, db, failed_node_ids, failed_link_ids)
        except Exception as e:
            return json.dumps({"error": f"What-if analysis failed: {e}"}), "", None

        # Build readable summary
        summary_parts = []
        taking_down = []
        for nid in failed_node_ids:
            taking_down.append(f"Node: {node_name_map.get(nid, f'#{nid}')}")
        for lid in failed_link_ids:
            taking_down.append(f"Link: {link_name_map.get(lid, f'#{lid}')}")
        summary_parts.append(f"Taking down: {', '.join(taking_down)}")
        summary_parts.append(f"Broken flows: {result['broken_count']}")
        summary_parts.append(f"Rerouted flows: {result['rerouted_count']}")
        summary_parts.append(f"Unaffected flows: {result['unaffected_count']}")
        if result['capacity_exceeded']:
            overloaded = [f"{ol['name']} ({ol['utilization_pct']:.0f}%)" for ol in result['overloaded_links']]
            summary_parts.append(f"CAPACITY EXCEEDED on: {', '.join(overloaded)}")
        if result['isolated_node_names']:
            summary_parts.append(f"Isolated nodes: {', '.join(result['isolated_node_names'])}")

        # Affected flows detail
        affected = []
        for af in result['affected_flows']:
            affected.append({"flow": af['flow_name'], "status": af['status']})

        # Utilization changes
        baseline_map = {lu['link_id']: lu for lu in result.get('baseline_utilizations', [])}
        util_changes = []
        for flu in result.get('failed_utilizations', []):
            bl = baseline_map.get(flu['link_id'])
            before = bl['utilization_pct'] if bl else 0
            delta = flu['utilization_pct'] - before
            if abs(delta) > 0.1:
                link_name = link_name_map.get(flu['link_id'], f"Link #{flu['link_id']}")
                util_changes.append({
                    "link": link_name,
                    "before": f"{before:.1f}%",
                    "after": f"{flu['utilization_pct']:.1f}%",
                    "delta": f"{'+' if delta > 0 else ''}{delta:.1f}%",
                })

        output = {
            "summary": "\n".join(summary_parts),
            "taking_down": taking_down,
            "broken_count": result['broken_count'],
            "rerouted_count": result['rerouted_count'],
            "unaffected_count": result['unaffected_count'],
            "capacity_exceeded": result['capacity_exceeded'],
            "affected_flows": affected,
            "overloaded_links": [{"name": ol['name'], "utilization": f"{ol['utilization_pct']:.0f}%"} for ol in result['overloaded_links']],
            "isolated_nodes": result['isolated_node_names'],
            "utilization_changes": util_changes[:20],  # Top 20 changes
        }
        desc = f"What-if: removed {len(taking_down)} element(s) — {result['broken_count']} broken, {result['rerouted_count']} rerouted"
        what_if_data = {
            "maintenance_node_ids": failed_node_ids,
            "maintenance_link_ids": failed_link_ids,
            "maintenance_result": result,
        }
        return json.dumps(output, indent=2), desc, what_if_data

    if tool_name == "list_maintenance_presets":
        from app.models.maintenance_preset import MaintenancePreset
        presets = db.query(MaintenancePreset).filter(MaintenancePreset.topology_id == topology_id).all()
        if not presets:
            return json.dumps({"presets": [], "message": "No saved presets. Use save_maintenance_preset to create one."}), "", None
        node_name_map = {n.id: n.name for n in db.query(NodeInstance).filter(NodeInstance.topology_id == topology_id).all()}
        result = []
        for p in presets:
            result.append({
                "id": p.id, "name": p.name, "description": p.description or "",
                "nodes": [node_name_map.get(nid, f"#{nid}") for nid in p.node_ids],
                "links": [f"Link #{lid}" for lid in p.link_ids],
            })
        return json.dumps({"presets": result}, indent=2), f"Found {len(presets)} preset(s)", None

    if tool_name == "save_maintenance_preset":
        import json as _json
        from app.models.maintenance_preset import MaintenancePreset
        name = tool_input.get("name", "")
        if not name:
            return json.dumps({"error": "Name is required"}), "", None
        preset = MaintenancePreset(
            topology_id=topology_id,
            name=name,
            node_ids_json=_json.dumps(tool_input.get("node_ids", [])),
            link_ids_json=_json.dumps(tool_input.get("link_ids", [])),
        )
        db.add(preset)
        db.commit()
        return json.dumps({"success": True, "id": preset.id, "name": name}), f"Saved preset '{name}'", None

    if tool_name == "clear_maintenance":
        return json.dumps({"success": True, "message": "All nodes and links restored to service."}), \
            "Cleared maintenance", \
            {"maintenance_node_ids": [], "maintenance_link_ids": [], "maintenance_result": None}

    if tool_name == "check_prometheus_status":
        from app.models.prometheus_config import PrometheusConfig
        from app.services.prometheus_service import PrometheusClient, PrometheusError

        cfg = db.query(PrometheusConfig).filter(PrometheusConfig.topology_id == topology_id).first()
        if not cfg:
            return json.dumps({
                "status": "not_configured",
                "message": "No Prometheus server is configured for this topology.",
                "how_to_fix": "The user needs to go to the Prometheus panel (Activity icon in the toolbar) and configure a Prometheus URL. If running in Docker Compose, use 'http://prometheus:9090'. For external servers, use the server's IP and port.",
            }), "", None

        client = PrometheusClient(url=cfg.url, auth_type=cfg.auth_type,
                                   username=cfg.username, password=cfg.password, bearer_token=cfg.bearer_token)
        result = {"url": cfg.url, "enabled": cfg.enabled, "rate_interval": cfg.rate_interval}

        # Test connection
        try:
            build_info = client.test_connection()
            result["connected"] = True
            result["prometheus_version"] = build_info.get("version", "unknown")
        except PrometheusError as e:
            result["connected"] = False
            result["connection_error"] = str(e)
            hints = []
            if "localhost" in cfg.url or "127.0.0.1" in cfg.url:
                hints.append("Using localhost won't work in Docker. Use 'http://prometheus:9090' (Docker service name) or 'http://host.docker.internal:9090'.")
            if not cfg.url.startswith("http"):
                hints.append("URL must start with http:// or https://")
            result["troubleshooting_hints"] = hints
            return json.dumps(result, indent=2), "Checked Prometheus status", None

        # Check SNMP targets
        try:
            targets = client.get_targets()
            snmp_targets = [t for t in targets if t.get("labels", {}).get("job") == "snmp"]
            result["snmp_targets"] = []
            for t in snmp_targets:
                result["snmp_targets"].append({
                    "instance": t.get("labels", {}).get("instance", "?"),
                    "health": t.get("health", "unknown"),
                    "last_error": t.get("lastError", ""),
                })
            result["snmp_targets_up"] = sum(1 for t in snmp_targets if t.get("health") == "up")
            result["snmp_targets_total"] = len(snmp_targets)
        except PrometheusError as e:
            result["targets_error"] = str(e)

        # Check node mapping
        nodes = db.query(NodeInstance).filter(NodeInstance.topology_id == topology_id).all()
        mapped = [n for n in nodes if n.prometheus_id]
        result["total_nodes"] = len(nodes)
        result["mapped_nodes"] = len(mapped)
        result["unmapped_nodes"] = [n.name for n in nodes if not n.prometheus_id]

        # Check available metrics
        try:
            sys_names = client.query("sysName")
            result["sysname_devices"] = [{"instance": r["metric"].get("instance", ""), "sysName": r["metric"].get("sysName", "")} for r in sys_names]
        except PrometheusError:
            result["sysname_devices"] = []

        issues = []
        if not result.get("snmp_targets"):
            issues.append("No SNMP targets found. The Prometheus server needs an 'snmp' job configured with network device targets.")
        if result.get("snmp_targets_up", 0) == 0 and result.get("snmp_targets_total", 0) > 0:
            issues.append("All SNMP targets are down. Check that SNMP credentials are correct and devices are reachable from the SNMP exporter.")
        if len(nodes) > 0 and len(mapped) == 0:
            issues.append("No nodes have Prometheus IDs. Edit each node and set the 'Prometheus ID' to match the device's IP/hostname in Prometheus.")
        if not result.get("sysname_devices"):
            issues.append("No sysName data found. The SNMP exporter may not be collecting sysName OID (1.3.6.1.2.1.1.5).")
        result["issues"] = issues
        result["status"] = "healthy" if not issues else "issues_found"

        return json.dumps(result, indent=2), "Checked Prometheus status", None

    if tool_name == "run_network_discovery":
        import asyncio
        from app.services.discovery import discover_topology, discover_topology_igp
        from app.routes.discovery import _build_topology_objects, _enrich_from_prometheus

        seed_ip = tool_input.get("seed_ip", "")
        os_type = tool_input.get("os_type", "iosxr")
        username = tool_input.get("username", "")
        password = tool_input.get("password", "")
        method = tool_input.get("discovery_method", "igp")
        igp_protocol = tool_input.get("igp_protocol", "isis")
        clear_existing = tool_input.get("clear_existing", True)

        if not seed_ip or not username or not password:
            return json.dumps({"error": "seed_ip, username, and password are required"}), "", None

        events = []
        async def on_progress(event_type, message, data=None):
            events.append({"type": event_type, "message": message, **(data or {})})

        try:
            loop = asyncio.new_event_loop()
            if method == "igp":
                result = loop.run_until_complete(discover_topology_igp(
                    seed_ip=seed_ip, seed_os=os_type, username=username, password=password,
                    igp_protocol=igp_protocol, timeout=30, on_progress=on_progress,
                ))
            else:
                result = loop.run_until_complete(discover_topology(
                    seed_ip=seed_ip, seed_os=os_type, username=username, password=password,
                    max_depth=5, max_devices=50, timeout_per_device=30, on_progress=on_progress,
                ))
            loop.close()
        except Exception as e:
            return json.dumps({
                "error": f"Discovery failed: {e}",
                "events": events,
                "troubleshooting": [
                    f"Could not connect to {seed_ip}. Check that the IP is correct and SSH is enabled.",
                    f"Verify credentials (username: {username}) are correct.",
                    f"Ensure the device OS type '{os_type}' matches the actual device.",
                    "If using IGP, ensure ISIS or OSPF is running on the seed device.",
                ],
            }, indent=2), "", None

        if not result or not result.devices:
            return json.dumps({
                "error": "No devices discovered",
                "events": events,
                "troubleshooting": [
                    f"Connected to {seed_ip} but found no topology data.",
                    f"For IGP ({igp_protocol}): verify the protocol is running with 'show {igp_protocol} adjacency' on the device.",
                    "For LLDP: verify LLDP is enabled with 'show lldp neighbors'.",
                ],
            }, indent=2), "", None

        # Build topology objects
        nodes_created, links_created = _build_topology_objects(topology_id, result, db, clear_existing)

        # Enrich from Prometheus
        enrichment = _enrich_from_prometheus(topology_id, db)

        summary = {
            "success": True,
            "nodes_created": nodes_created,
            "links_created": links_created,
            "devices": [{"hostname": h, "os_type": d.os_type, "vendor": d.vendor, "interfaces": len(d.interfaces)}
                        for h, d in result.devices.items()],
            "enrichment": enrichment,
            "events": events[-5:],  # last 5 events for context
        }
        return json.dumps(summary, indent=2), f"Discovered {nodes_created} nodes, {links_created} links", None

    if tool_name == "discover_prometheus_devices":
        from app.models.prometheus_config import PrometheusConfig
        from app.services.prometheus_service import PrometheusClient, PrometheusError

        cfg = db.query(PrometheusConfig).filter(PrometheusConfig.topology_id == topology_id).first()
        if not cfg:
            return json.dumps({"error": "No Prometheus configured. Set it up in the Prometheus panel first."}), "", None

        client = PrometheusClient(url=cfg.url, auth_type=cfg.auth_type,
                                   username=cfg.username, password=cfg.password, bearer_token=cfg.bearer_token)
        try:
            devices = client.discover_network_devices()
            return json.dumps({
                "devices": devices,
                "count": len(devices),
                "healthy": sum(1 for d in devices if d["health"] == "up"),
            }, indent=2), f"Discovered {len(devices)} devices from Prometheus", None
        except PrometheusError as e:
            return json.dumps({"error": str(e)}), "", None

    if tool_name == "diagnose_prometheus_data":
        from app.models.prometheus_config import PrometheusConfig
        from app.services.prometheus_service import PrometheusClient, PrometheusError

        cfg = db.query(PrometheusConfig).filter(PrometheusConfig.topology_id == topology_id).first()
        if not cfg:
            return json.dumps({
                "error": "No Prometheus configured",
                "how_to_fix": "Configure Prometheus in the Prometheus panel (Activity icon in toolbar) or use the configure_prometheus tool.",
            }), "", None

        client = PrometheusClient(url=cfg.url, auth_type=cfg.auth_type,
                                   username=cfg.username, password=cfg.password, bearer_token=cfg.bearer_token)
        try:
            client.test_connection()
        except PrometheusError as e:
            return json.dumps({"error": f"Cannot connect to Prometheus: {e}"}), "", None

        device_ip = tool_input.get("device_ip", "")
        instance_filter = f'{{instance="{device_ip}"}}' if device_ip else ""

        diagnostics = {"devices": []}
        checks = {
            "sysName": f"sysName{instance_filter}",
            "ifName": f"ifName{instance_filter}",
            "ifHighSpeed": f"ifHighSpeed{instance_filter}",
            "ifHCInOctets": f"ifHCInOctets{instance_filter}",
            "ifHCOutOctets": f"ifHCOutOctets{instance_filter}",
            "ifOperStatus": f"ifOperStatus{instance_filter}",
            "lldpRemSysName": f"lldpRemSysName{instance_filter}",
            "ipAdEntIfIndex": f"ipAdEntIfIndex{instance_filter}",
        }

        # Get all targets first
        try:
            targets = client.get_targets()
            snmp_targets = [t for t in targets if t.get("labels", {}).get("job") == "snmp"]
            if device_ip:
                snmp_targets = [t for t in snmp_targets if t.get("labels", {}).get("instance") == device_ip]
        except PrometheusError:
            snmp_targets = []

        # Run each check
        metric_results = {}
        for metric_name, query in checks.items():
            try:
                results = client.query(query)
                # Group by instance
                by_instance = {}
                for r in results:
                    inst = r["metric"].get("instance", "unknown")
                    by_instance.setdefault(inst, []).append(r["metric"])
                metric_results[metric_name] = by_instance
            except PrometheusError as e:
                metric_results[metric_name] = {"error": str(e)}

        # Build per-device diagnostics
        all_instances = set()
        for t in snmp_targets:
            all_instances.add(t.get("labels", {}).get("instance", ""))
        for metric_data in metric_results.values():
            if isinstance(metric_data, dict) and "error" not in metric_data:
                all_instances.update(metric_data.keys())

        for inst in sorted(all_instances):
            if not inst:
                continue
            target = next((t for t in snmp_targets if t.get("labels", {}).get("instance") == inst), None)
            dev_diag = {
                "instance": inst,
                "health": target.get("health", "unknown") if target else "not_a_target",
                "last_error": target.get("lastError", "") if target else "",
                "data_available": {},
                "issues": [],
                "recommendations": [],
            }

            # Check each metric
            for metric_name in checks:
                data = metric_results.get(metric_name, {})
                if isinstance(data, dict) and "error" in data:
                    dev_diag["data_available"][metric_name] = f"query error: {data['error']}"
                    continue
                entries = data.get(inst, [])
                dev_diag["data_available"][metric_name] = len(entries)

            # Analyze issues
            d = dev_diag["data_available"]
            if d.get("sysName", 0) == 0:
                dev_diag["issues"].append("No sysName data — hostname matching during discovery enrichment will fail")
                dev_diag["recommendations"].append("Verify SNMP sysName OID (1.3.6.1.2.1.1.5) is accessible with the configured SNMPv3 credentials")
            if d.get("ifName", 0) == 0:
                dev_diag["issues"].append("No interface names — cannot map interfaces to links")
                dev_diag["recommendations"].append("Verify IF-MIB walk is configured in snmp.yml and SNMP exporter can reach the device")
            if d.get("ifHighSpeed", 0) == 0:
                dev_diag["issues"].append("No interface speeds — bandwidth will use inferred values from interface names")
                dev_diag["recommendations"].append("Check if ifHighSpeed OID (1.3.6.1.2.1.31.1.1.1.15) returns data for this device")
            if d.get("ifHCInOctets", 0) == 0:
                dev_diag["issues"].append("No traffic counters — live metrics will show 0% utilization")
                dev_diag["recommendations"].append("Verify 64-bit counters (ifHCInOctets/ifHCOutOctets) are supported on this device")
            if d.get("lldpRemSysName", 0) == 0:
                dev_diag["issues"].append("No LLDP neighbor data — link interface matching will rely on IP subnet correlation instead")
                dev_diag["recommendations"].append("Enable LLDP on the device: 'lldp' in global config (IOS-XR), 'set protocols lldp interface all' (Junos)")
            if d.get("ipAdEntIfIndex", 0) == 0:
                dev_diag["issues"].append("No IP address data — subnet-based link interface correlation will not work")
                dev_diag["recommendations"].append("Verify ipAddrTable OID (1.3.6.1.2.1.4.20) is accessible")

            if not dev_diag["issues"]:
                dev_diag["status"] = "all_data_available"
            else:
                dev_diag["status"] = f"{len(dev_diag['issues'])} issue(s)"

            diagnostics["devices"].append(dev_diag)

        # Summary
        total_issues = sum(len(d["issues"]) for d in diagnostics["devices"])
        diagnostics["summary"] = {
            "devices_checked": len(diagnostics["devices"]),
            "total_issues": total_issues,
            "status": "healthy" if total_issues == 0 else f"{total_issues} issue(s) found across {sum(1 for d in diagnostics['devices'] if d['issues'])} device(s)",
        }

        return json.dumps(diagnostics, indent=2), "Diagnosed Prometheus data", None

    if tool_name == "configure_prometheus":
        from app.models.prometheus_config import PrometheusConfig

        url = tool_input.get("url", "")
        if not url:
            return json.dumps({"error": "url is required"}), "", None

        existing = db.query(PrometheusConfig).filter(PrometheusConfig.topology_id == topology_id).first()
        if existing:
            existing.url = url
            for field in ["auth_type", "username", "password", "bearer_token", "enabled"]:
                if field in tool_input:
                    setattr(existing, field, tool_input[field])
            db.commit()
            return json.dumps({"success": True, "message": f"Updated Prometheus config: {url}", "id": existing.id}), f"Configured Prometheus: {url}", None

        cfg = PrometheusConfig(
            topology_id=topology_id,
            url=url,
            auth_type=tool_input.get("auth_type", "none"),
            username=tool_input.get("username", ""),
            password=tool_input.get("password", ""),
            bearer_token=tool_input.get("bearer_token", ""),
            enabled=tool_input.get("enabled", False),
        )
        db.add(cfg)
        db.commit()
        return json.dumps({"success": True, "message": f"Created Prometheus config: {url}", "id": cfg.id}), f"Configured Prometheus: {url}", None

    if tool_name == "enrich_from_prometheus":
        from app.routes.discovery import _enrich_from_prometheus
        from app.models.prometheus_config import PrometheusConfig as _PC
        cfg = db.query(_PC).filter(_PC.topology_id == topology_id).first()
        if not cfg:
            return json.dumps({
                "error": "No Prometheus configured for this topology.",
                "how_to_fix": "Use configure_prometheus tool or set it up in the Prometheus panel first.",
            }), "", None
        try:
            result = _enrich_from_prometheus(topology_id, db)
            if result.get("enriched"):
                summary = (
                    f"Enrichment complete: matched {result['matched_nodes']}/{result['total_nodes']} nodes to Prometheus targets, "
                    f"renamed {result.get('interfaces_renamed', 0)} interfaces, "
                    f"updated {result.get('interfaces_updated', 0)} speeds from SNMP, "
                    f"added {result.get('interfaces_added', 0)} new interfaces, "
                    f"filled {result.get('links_iface_filled', 0)} link interfaces (LLDP + IP subnet), "
                    f"corrected {result.get('links_updated', 0)} link bandwidths"
                )
                result["summary"] = summary
            return json.dumps(result, indent=2), "Enriched topology from Prometheus", None
        except Exception as e:
            return json.dumps({"error": f"Enrichment failed: {e}"}), "", None

    if tool_name == "get_live_metrics":
        from app.models.prometheus_config import PrometheusConfig
        from app.services.prometheus_service import PrometheusClient, PrometheusError

        cfg = db.query(PrometheusConfig).filter(PrometheusConfig.topology_id == topology_id).first()
        if not cfg:
            return json.dumps({"error": "No Prometheus configured for this topology."}), "", None
        if not cfg.enabled:
            return json.dumps({"error": "Live metrics not enabled. Toggle the Live switch in the Prometheus panel."}), "", None

        client = PrometheusClient(url=cfg.url, auth_type=cfg.auth_type,
                                   username=cfg.username, password=cfg.password, bearer_token=cfg.bearer_token)
        nodes = db.query(NodeInstance).filter(NodeInstance.topology_id == topology_id).all()
        links_db = db.query(Link).filter(Link.topology_id == topology_id).all()

        node_dicts = [{"id": n.id, "name": n.name, "prometheus_id": n.prometheus_id or "", "interfaces": n.interfaces} for n in nodes]
        link_dicts = [{"id": l.id, "source_node_id": l.source_node_id, "target_node_id": l.target_node_id,
                       "source_interface": l.source_interface or "", "target_interface": l.target_interface or "",
                       "bandwidth_gbps": l.bandwidth_gbps} for l in links_db]

        config_dict = {
            "in_octets_metric": cfg.in_octets_metric, "out_octets_metric": cfg.out_octets_metric,
            "speed_metric": cfg.speed_metric, "interface_label": cfg.interface_label,
            "instance_label": cfg.instance_label, "rate_interval": cfg.rate_interval,
        }

        try:
            result = client.get_live_metrics(node_dicts, link_dicts, config_dict)
            # Build a human-readable summary
            node_map = {n.id: n.name for n in nodes}
            summary_lines = []
            for lu in sorted(result["link_utilizations"], key=lambda x: -x["utilization_pct"]):
                src = node_map.get(lu["source_node_id"], "?")
                tgt = node_map.get(lu["target_node_id"], "?")
                summary_lines.append(f"{src} <-> {tgt}: {lu['utilization_pct']}% ({lu['used_gbps']}G / {lu['bandwidth_gbps']}G)")
            result["summary"] = "\n".join(summary_lines) if summary_lines else "No link data available"
            return json.dumps(result, indent=2), "Fetched live metrics", None
        except PrometheusError as e:
            return json.dumps({"error": str(e)}), "", None

    return "Unknown tool", "", None


class ChatRequest(BaseModel):
    messages: list[dict]


@router.post("")
def chat(topology_id: int, req: ChatRequest, db: Session = Depends(get_db)):
    """Streaming chat endpoint using Server-Sent Events.
    Tool calls are executed server-side (non-streamed), then the final
    text response is streamed token-by-token."""
    err = llm.config_error()
    if err:
        def no_key():
            msg = f"I'm not configured yet. {err}"
            yield f"data: {json.dumps({'type': 'token', 'text': msg})}\n\n"
            yield f"data: {json.dumps({'type': 'done', 'actions_performed': []})}\n\n"
        return StreamingResponse(no_key(), media_type="text/event-stream")

    def generate():
        # Fast model (PHANERO_AI_TOOL_MODEL) for tool selection, main model for
        # analytical final responses. Same model on providers without a split.
        conv = llm.Conversation(req.messages, TOOLS)

        actions_performed = []
        extra_data = {}
        used_tools = False

        try:
            # Tool use loop (non-streamed — tools must complete before we can stream the final response)
            max_iterations = 10
            for _ in range(max_iterations):
                turn = conv.step(SYSTEM_PROMPT, model=llm.TOOL_MODEL)

                if turn.tool_calls:
                    used_tools = True
                    tool_results = []
                    for call in turn.tool_calls:
                        # Send a status event so the frontend knows a tool is running
                        yield f"data: {json.dumps({'type': 'tool_status', 'tool': call.name})}\n\n"
                        result_text, action_desc, data = _execute_tool(
                            call.name, call.input, topology_id, db
                        )
                        if action_desc:
                            actions_performed.append(action_desc)
                        if data:
                            extra_data.update(data)
                        tool_results.append((call.id, result_text))

                    conv.add_tool_results(tool_results)
                    continue

                # Final response — no more tools needed.
                # If tools were used, re-generate with the main model for better analysis of the data.
                # If no tools were used (simple question), use the tool model's response directly.
                text = turn.text
                if used_tools and llm.MODEL != llm.TOOL_MODEL:
                    text = conv.step(SYSTEM_PROMPT, model=llm.MODEL, allow_tools=False).text

                chunk_size = 20
                for i in range(0, len(text), chunk_size):
                    yield f"data: {json.dumps({'type': 'token', 'text': text[i:i+chunk_size]})}\n\n"
                break
        except Exception as e:
            logger.exception("AI chat failed")
            yield f"data: {json.dumps({'type': 'token', 'text': f'AI assistant error: {e}'})}\n\n"

        # Send metadata at the end
        yield f"data: {json.dumps({'type': 'done', 'actions_performed': actions_performed, **extra_data})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")
