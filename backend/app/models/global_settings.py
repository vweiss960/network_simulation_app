from sqlalchemy import Column, Integer, String, Float
from app.models.database import Base


class GlobalSettings(Base):
    __tablename__ = "global_settings"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String, unique=True, nullable=False)
    value = Column(Float, nullable=False)
    description = Column(String, default="")


DEFAULT_SETTINGS = {
    "default_node_mtbf_hours": (50000.0, "Default MTBF for nodes in hours"),
    "default_node_mttr_hours": (4.0, "Default MTTR for nodes in hours"),
    "default_link_mtbf_hours": (80000.0, "Default MTBF for links in hours"),
    "default_link_mttr_hours": (2.0, "Default MTTR for links in hours"),
    "monte_carlo_iterations": (10000.0, "Number of Monte Carlo simulation iterations"),
    "cost_per_gb_default": (0.05, "Default cost per GB for links"),
    "latency_route_factor": (1.5, "Latency estimate: fiber route length vs. straight-line distance"),
    "latency_overhead_ms": (0.1, "Latency estimate: fixed per-link equipment delay (ms)"),
    "latency_unknown_penalty_ms": (10.0, "Lowest-latency routing: assumed latency of a link with no value or estimate (ms; not added to reported totals)"),
    "sat_leo_altitude_km": (550.0, "Satellite latency estimate: LEO orbit altitude (km)"),
    "sat_meo_altitude_km": (8062.0, "Satellite latency estimate: MEO orbit altitude (km)"),
    "sat_geo_altitude_km": (35786.0, "Satellite latency estimate: GEO orbit altitude (km)"),
    "sat_leo_processing_ms": (10.0, "Satellite latency estimate: LEO one-way processing / scheduling delay (ms)"),
    "sat_meo_processing_ms": (10.0, "Satellite latency estimate: MEO one-way processing delay (ms)"),
    "sat_geo_processing_ms": (25.0, "Satellite latency estimate: GEO one-way processing delay (ms)"),
}
