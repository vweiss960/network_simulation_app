# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Phanero — a network modeling app. Backend: FastAPI + SQLAlchemy + SQLite, graph
analysis with NetworkX. Frontend: React + Vite + TypeScript, Zustand for state,
React Flow for the logical canvas, MapLibre GL for the geographic view. Runs via
Docker Compose.

## Running & building

Everything runs in Docker Compose; there is no host-level dev setup.

```bash
docker compose up -d                       # dev stack (Vite dev server)
docker compose logs -f backend             # logs
```

Services: an edge **nginx** (host ports 80 / 3000-https) proxies `/api/` →
backend and everything else → frontend. **Backend** = uvicorn on `:8000`
(host network mode, for containerlab netlink access). **Frontend** = Vite dev
server on `:5173`. Plus `prometheus` and `snmp-exporter` for the live-metrics
feature.

```bash
# Production: builds the frontend bundle + serves it via nginx (the dev Vite
# server is slow to load remotely). Only the frontend service differs.
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

### Critical: the dev backend does NOT hot-reload

uvicorn runs with `--reload`, but its file watcher does not fire across the
Docker bind mount. After editing **backend** code you must restart, and after
changing **env vars** you must recreate (env is set at container creation):

```bash
docker compose restart backend      # picked up code changes
docker compose up -d backend        # picks up .env / compose env-var changes
```

The frontend Vite server *does* hot-reload over the mount.

## Verifying changes

- **Backend tests (pytest):** cover the graph/analytics engine (`services/
  simulation.py`), inheritance merge (`services/topology_chain.py`), and link
  validation. Pytest is a dev-only dep (not in the runtime image), so install it
  on first run:
  ```bash
  docker compose exec -T backend sh -c "pip install -q -r requirements-dev.txt && cd /app && python -m pytest"
  ```
  Tests live in `backend/tests/` (`conftest.py` binds the app engine to a
  throwaway temp SQLite DB before importing the app; `factories.py` seeds small
  topologies). Add tests when you touch the simulation/inheritance logic.
- **Frontend type-check:** `docker compose exec -T frontend sh -c "cd /app && npx tsc --noEmit"`
  (note: the repo has pre-existing `tsc` errors, e.g. in `SimulationPanel.tsx`'s
  MonteCarlo section — grep for the file you touched to check you added none).
- **Frontend bundle build:** `npm run build` inside the frontend container (`tsc && vite build`).
  There are **no frontend tests yet** (Tier 3 in the test plan).
- **Ad-hoc backend checks:** exercise functions/endpoints directly, e.g.
  `docker compose exec -T backend python3 -c "import app.main; from app.services import simulation; ..."`.
  `import app.main` first to register all SQLAlchemy mappers before importing models standalone.

## Architecture

### Topology inheritance (parent / child planning layers)
A topology may have a `parent_topology_id`. A **child** ("planning layer")
inherits its parent's nodes/links and adds local ones; local items can shadow
inherited ones, and a parent link can "absorb" a child planning link.
`backend/app/services/topology_chain.py:get_merged_topology_data(db, topology_id)`
returns `(nodes, links, flows, node_remap)` for the merged view — **most backend
analysis loads data through it**, not from raw queries. Inheritance pervades the
UI too (inherited edges render with a halo, planning-local links are yellow,
etc.), so behavior often differs for child topologies — check `is_inherited` /
`isChildTopology`.

### Backend layout
`backend/app/`: `routes/` (one router per feature), `services/` (logic —
`simulation.py` is the core: flow routing, utilization, SPOF/failure scenarios,
Monte Carlo, maintenance window, SRLG, all NetworkX), `models/` (SQLAlchemy +
Pydantic `schemas/`), `auth/` (JWT). Routers are mounted in `main.py` behind
`auth_dep`; the lab-console WebSocket authenticates via `?token=<jwt>`.

DB is SQLite at `backend/data/network_modeler.db`. Schema is `create_all()` plus
an **additive** `_migrate_db()` in `main.py` — add new columns there with
`ALTER TABLE ... ADD COLUMN ... DEFAULT ...` (idempotent, guarded by an
`inspector` column check). No Alembic.

### Simulation cost model
`run_simulation()` splits into a cheap **utilization** pass (one flow-path solve)
and an expensive **failure analysis** (re-solves per node + per link failure —
~98% of the time, scales with node+link count). It takes `failure_analysis=bool`;
the frontend runs util-only by default and triggers SPOF on demand. SPOF/maintenance/
SRLG isolation excludes **baseline-isolated** nodes (already disconnected with
nothing failed) so they aren't blamed on every scenario.

### Feature modules (env-var gated)
Optional features are gated by `PHANERO_*_ENABLED` env vars (default OFF):
`LAB`, `CONFIG_DESIGN`, `GEO`, `AI`, `DISCOVERY`, `PROMETHEUS`, `COST`,
`INTEGRATIONS`. Pattern: in `main.py` the feature's router is registered only
when its flag is set (disabled → endpoints 404); `GET /api/features` returns all
flags; the frontend loads them once into the Zustand store (`loadFeatures`) and
`Toolbar`/`SidePanel`/`App` hide the menu/toggle/panel accordingly. The Device
Templates menu is gated behind the Lab flag. Vars are documented in
`.env.example` (and `docker-compose.yml`); `.env` is gitignored.

### AI provider
The AI assistants (`routes/chat.py`, `config_ai.py`, `service_ai.py`) never call
an SDK directly — they go through `services/llm.py` (`Conversation.step()` /
`add_tool_results()`), which picks the provider from `PHANERO_AI_PROVIDER`
(`anthropic` | `openai` | `litellm` — the latter two share the OpenAI-compatible
adapter). Tools stay defined in Anthropic shape (`input_schema`) and are
translated per provider.

### Frontend layout
`frontend/src/stores/useStore.ts` is the central Zustand store (topologies,
nodes, links, flows, simulation results, feature flags, UI panel state).
`TopologyEditor.tsx` renders either the React Flow logical canvas (edges built in
the `rfEdges` `useMemo`, drawn by `CustomEdge.tsx`) or `GeographicCanvas.tsx`
(MapLibre globe) depending on `viewMode`. `Toolbar.tsx` is the left nav,
`SidePanel.tsx` switches the right-hand `panels/*`. `utils/api.ts` wraps all
backend calls.

Nodes carry **two** coordinate systems: logical `x`/`y` (projected to/from
`latitude`/`longitude`) for the React Flow canvas, and independent
`geo_latitude`/`geo_longitude` for the geographic map. Links can carry a
geo-view-only `geo_path` (a fiber route the link follows), which may be a single
line or **multi-part** `[[[lng,lat],...],...]`.

### Geographic view
MapLibre globe with a Protomaps basemap configured from env (read at runtime via
`/api/features` `map` block — no rebuild to switch sources). `routes/map_data.py`
proxies + disk-caches external data (TeleGeography submarine cables, PeeringDB
data centers/IXPs, CelesTrak satellites, NWS/USGS/NIFC/NHC/IODA real-time
overlays, Nominatim geocode) and a heuristic PeeringDB interconnection path
finder. KMZ/KML files are parsed client-side and can be stitched into link routes
(see the Path Builder in `GeographicCanvas.tsx`).
