"""In-app documentation for the AI agent. Loaded on-demand via tool call."""

APP_DOCS = {
    "overview": {
        "title": "Phanero Overview",
        "content": """Phanero is a network modeling, simulation, and cost allocation tool. The main interface has:
- **Left sidebar**: Vertical toolbar with icon buttons for each feature panel
- **Center**: Interactive topology canvas (drag to pan, scroll to zoom, right-click for context menu)
- **Right**: Side panel that opens when you click a toolbar button
- **Top bar**: Topology selector dropdown, API docs icon (book icon), admin settings (gear icon), user management (people icon), logout

The toolbar icons from top to bottom are:
1. Box icon = Nodes
2. Link icon = Links
3. Branch icon = Flows/Demands
4. Play icon = Simulation
5. Layers icon = SRLGs
6. Wrench icon = Maintenance Window
7. Grid icon = Device Templates
8. Radar icon = Network Discovery
9. People icon = Customers
10. Dollar icon = Cost Analysis
11. Activity icon = Prometheus
12. Gear icon = Global Settings
--- separator ---
13. Chat bubble (purple) = AI Assistant
Bottom: Palette icon = Color scheme picker"""
    },

    "nodes": {
        "title": "Managing Nodes",
        "content": """**Panel**: Click the Box icon (1st in toolbar) to open the Nodes panel.

**Adding a node**:
- Click the blue "+ Add Node" button at the top of the panel
- Enter a name, select a device template (optional), set location, coordinates
- Click "Add" or press Enter to save
- Alternative: Drag a template from the Templates panel onto the canvas

**Editing a node**:
- Click a node in the list to expand it, then click the pencil icon
- Or right-click a node on the canvas → "Edit"
- Editable fields: name, vendor, model, platform, location, lat/lng, MTBF/MTTR, capacity, Prometheus ID, Management IP, Planned checkbox
- Click "Save" or press Enter to save changes

**Planned nodes**: Check "Planned (not yet built)" in the edit form. Planned nodes appear with a dashed indigo circle and "PLANNED" label on the canvas.

**Hide Planned toggle**: Below the "Group by Location" toggle. Hides all planned nodes and their connected links from the canvas.

**Deleting a node**: Click the red trash icon next to the node, or right-click on canvas → "Delete Node"

**Right-click menu on canvas nodes**: Edit, Add Link from here, SSH to device (if mgmt_ip set), Delete

**Search**: Use the search bar below the toggles to filter nodes by name, vendor, model, or location."""
    },

    "links": {
        "title": "Managing Links",
        "content": """**Panel**: Click the Link icon (2nd in toolbar) to open the Links panel.

**Adding a link**:
- Click "+ Add Link" button, select source/target nodes and interfaces
- Interfaces must match speeds (e.g. both 10G)
- Set latency, carrier, circuit cost, circuit ID, link ID
- Alternative: Right-click a node on canvas → "Add Link from here" → click the target node

**Editing a link**: Click a link in the list to expand, then click the pencil icon. Or right-click a link on the canvas → "Edit".

**Link Labels toggle**: At the top of the Links panel. Turn it on, then select which attributes to show on the topology:
- Interface, IP Address, Bandwidth, Cost, Latency, Carrier, Circuit Cost, Circuit ID, Link ID
- Labels appear near both endpoints of each link

**Circuit Cost**: Monthly cost to keep the circuit active. Used by the Cost Allocation engine. Different from routing "Cost" which determines shortest path.

**Planned links**: Check "Planned (not yet built)" in the edit form. Planned links appear as dashed indigo lines."""
    },

    "flows": {
        "title": "Managing Flows / Demands",
        "content": """**Panel**: Click the Branch icon (3rd in toolbar) to open the Flows/Demands panel.

**Adding a flow**:
- Click "+ Add Flow", enter name, select source (A) and destination (Z) nodes
- Set bandwidth (Gbps or Mbps), routing behavior
- Routing options: Shortest Path (by cost), Lowest Latency, Optimized (dynamic), Explicit Path
- Check ECMP to split traffic across equal-cost paths
- Click "Add" or press Enter

**Importing flows**: Click "Import" button → upload Excel/CSV or paste from clipboard. The wizard maps columns to flow fields and supports description concatenation.

**Include Planned toggle**: When on, planned nodes/links are included in the simulation so flows can route through them.

**Customer ownership**: A flow belongs to a customer automatically when its source or destination is a customer-role node assigned to that customer (set in the Nodes panel). A flow between two customers' nodes belongs to both.

**Flow colors in list**:
- Red border = no path found (broken)
- Indigo border = depends on planned infrastructure (shows dependency list)
- Normal = healthy

**Path display**: Each flow shows its computed path below the name, with latency in ms."""
    },

    "simulation": {
        "title": "Simulation & Analysis",
        "content": """**Panel**: Click the Play icon (4th in toolbar) to open the Simulation panel.

**Running simulation**: Click "Re-run Simulation" or simulation auto-runs when you add links, change link cost/bandwidth, add/modify flows.

**Tabs**:
- **Overview**: Summary cards (flows routed, SPOFs, max utilization) + flow paths with bandwidth and latency per path
- **Utilization**: Per-link utilization bars sorted by percentage. Click a link to select it on the canvas.
- **Failures**: Single points of failure analysis. Shows which nodes/links would isolate parts of the network.
- **Monte Carlo**: Time-based availability simulation.

**Monte Carlo**:
- Enter A-location and Z-location (node name or location)
- Select specific devices if multiple match
- Set iterations (more = more precise, slower)
- "Include Planned" toggle to include/exclude planned infrastructure
- Results show availability %, annual downtime, std deviation, disjoint paths"""
    },

    "maintenance": {
        "title": "Maintenance Window",
        "content": """**Panel**: Click the Wrench icon (6th in toolbar) to open Maintenance.

**Putting nodes/links in maintenance**:
- Click items in the node/link lists to toggle them into maintenance (they highlight red with "MAINT" badge)
- Maintenance nodes turn red on the canvas, connected links also turn red
- Click "Apply and Re-simulate" to run the analysis

**Results show**:
- Broken/Rerouted/Unaffected flow counts
- Capacity exceeded warnings
- Affected flows (click to expand and see A/Z locations)
- Overloaded links with utilization
- Link utilization changes (before/after/delta)

**Failure Presets**:
- Click "Save" in the maintenance banner to save current selection as a named preset
- Presets section shows saved scenarios — click to load, trash icon to delete
- Presets persist across sessions

**Clearing maintenance**: Click "Clear" in the banner, or toggle items back off."""
    },

    "customers": {
        "title": "Customer Management",
        "content": """**Panel**: Click the People icon (9th in toolbar) to open Customers.

**Adding a customer**: Click "+ Add Customer", enter name, email, phone, notes.

**Assigning flows**: Flows are not assigned directly. Edit a node, set its role to Customer and pick the customer — every flow starting or ending on that node then belongs to the customer.

**Viewing customer flows**: Expand a customer to see its nodes (click one to center it on the canvas) and every flow touching them. Click a flow to highlight its path; right-click to open it in the Flows panel."""
    },

    "cost_analysis": {
        "title": "Cost Allocation",
        "content": """**Panel**: Click the Dollar icon (10th in toolbar) to open Cost Allocation.

**Prerequisites**:
- Links need "Circuit Cost" set (edit link → Circuit Cost field)
- Flows must start or end on customer nodes assigned to a customer (Nodes panel); a flow between two customers is split evenly between them

**Allocation Models** (click the info icon for comparison table):
- **Proportional to Used Bandwidth**: Full circuit cost split among customers by their share of used bandwidth. Provider recovers full cost. Customers absorb idle capacity cost.
- **Proportional to Total Capacity**: Customers pay for their share of total link capacity only. Unused capacity cost stays with provider.

**Running analysis**: Select model → click "Run Cost Allocation"

**Results tabs**:
- **Overview**: Total cost, allocated/unallocated, customer ranking, CSV export button
- **By Customer**: Expandable per-customer view with per-link cost breakdown
- **By Link**: Expandable per-link view with formula transparency showing exact calculation

**CSV Export**: Click "Export Full Report (CSV)" in Overview tab — includes customer summary, detail, link breakdown, and allocation formulas."""
    },

    "color_schemes": {
        "title": "Color Schemes",
        "content": """**Location**: Palette icon at the bottom of the left toolbar. Click to open (click outside to close).

**Options**:
- **Utilization**: Blue (0%) → Green (<25%) → Lime (<50%) → Yellow (<75%) → Orange (<90%) → Red (>90%)
- **Cost/GB**: Shows circuit_cost / used_gbps per link. Yellow (low) → Purple (high). Dash if no traffic.
- **Circuit Cost**: Shows monthly circuit cost. Light blue (no cost) → Yellow (low) → Purple (average) → Deep purple (expensive). Centered on average cost across all links.

Each scheme shows its legend at the bottom-left of the topology canvas."""
    },

    "prometheus": {
        "title": "Prometheus Integration",
        "content": """**Panel**: Click the Activity icon (11th in toolbar) to open Prometheus.

**Two modes**:
- **Built-in (Managed)**: App manages Prometheus + SNMP Exporter containers. Configure SNMP credentials (v2c/v3) and the app generates configs automatically.
- **External Server**: Point to an existing Prometheus instance with SNMP data.

**Configuration tab**: Set mode, URL/credentials, rate interval. "Test Connection" validates connectivity. "Enrich Topology" pulls SNMP data into existing nodes/links.

**Live Metrics tab**: Toggle live mode on/off. Refresh for current data. Historical view shows avg/max over 1-30 days.

**Discovery tab**: Discover devices from SNMP targets. Preview interfaces and health. Import to topology.

**Node mapping**: Each node needs a "Prometheus ID" (set in node edit form) matching the Prometheus instance label. Discovery auto-sets this."""
    },

    "discovery": {
        "title": "Network Discovery",
        "content": """**Panel**: Click the Radar icon (8th in toolbar) to open Discovery.

**Methods**:
- **IGP (ISIS/OSPF)**: SSH into a seed device, pull the full link-state database. Gets all routers and adjacencies in one shot.
- **LLDP**: Recursive crawl via LLDP/CDP neighbor discovery.

**Running discovery**: Enter seed IP, OS type, SSH credentials. Click "Discover".

**Prometheus enrichment**: If Prometheus is configured, discovery automatically enriches nodes with real interface speeds, names, IPs, and management IPs from SNMP data."""
    },

    "topology_management": {
        "title": "Topology Management",
        "content": """**Home screen**: Click the House icon (top-left) to go back to the topology list.

**Creating a topology**: Click "+ New Topology" on the home screen.

**Copying**: Click the copy icon on a topology card.

**Exporting**: When inside a topology, click the Download icon next to the topology name dropdown:
- **JSON**: Machine-readable format for import
- **YAML**: Human-readable format
- **Offline Viewer (HTML)**: Self-contained interactive HTML file viewable in any browser. Includes utilization colors, SPOF panel, node/link/flow details.

**Importing**: On the home screen, click "Import" and upload a JSON or YAML file.

**Renaming**: Click the edit icon on the topology card on the home screen."""
    },

    "keyboard_shortcuts": {
        "title": "Keyboard & Mouse Shortcuts",
        "content": """**On the topology canvas**:
- Left-click drag on empty space: Pan the map
- Scroll wheel: Zoom in/out
- Right-click drag: Box select multiple nodes
- Shift+click nodes: Multi-select
- Delete/Backspace: Delete selected node(s)
- Escape: Cancel link drawing or path building

**In forms**:
- Enter: Save/submit the current form
- Escape: Cancel editing

**Right-click context menus**:
- On a node: Edit, Add Link, SSH (if mgmt_ip), Delete
- On a link: Edit, Delete
- On empty canvas: Add Node (from template list)"""
    },

    "planned_infrastructure": {
        "title": "Planned Infrastructure",
        "content": """**Purpose**: Model nodes and links that are planned but not yet built.

**Marking as planned**: Edit a node or link → check "Planned (not yet built)"

**Visual appearance**:
- Planned nodes: Dashed indigo circle, "PLANNED" label, reduced opacity
- Planned links: Dashed indigo line, reduced opacity
- "PLANNED" badge in node list

**Simulation behavior**:
- Default: Planned elements EXCLUDED from simulation and Monte Carlo
- "Include Planned" toggle (Flows panel and Monte Carlo section): Include them
- Flows depending on planned elements show indigo border with dependency list

**Hide Planned toggle** (Nodes panel): Completely hides planned elements from canvas. Also disables the Include Planned toggles in other panels.

**Export/Import**: Planned status is preserved in topology export/import."""
    },
}


def get_doc_topics() -> list[str]:
    """Return list of available documentation topics."""
    return list(APP_DOCS.keys())


def get_doc(topic: str) -> dict | None:
    """Get documentation for a specific topic."""
    return APP_DOCS.get(topic)


def search_docs(query: str) -> list[dict]:
    """Search documentation by keyword."""
    query_lower = query.lower()
    results = []
    for key, doc in APP_DOCS.items():
        if query_lower in doc["title"].lower() or query_lower in doc["content"].lower():
            # Find the relevant line
            lines = doc["content"].split("\n")
            matching_lines = [l.strip() for l in lines if query_lower in l.lower()]
            results.append({
                "topic": key,
                "title": doc["title"],
                "matches": matching_lines[:3],
            })
    return results
