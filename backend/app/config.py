"""
Centralized Configuration — Dynamic Multi-Modal Dark Store Mesh Network Optimizer

All runtime-tunable parameters extracted from the existing codebase into a single,
type-safe, environment-overridable settings object. Every default value preserves
the exact behavior of the current production system so that importing this module
changes nothing until a value is explicitly overridden.

Organized into six logical groups:
    1. Simulation   — tick rate, lambda scaling, product catalog
    2. Routing      — OR-Tools VRP-TW solver parameters
    3. Risk         — stockout/overstock classification thresholds
    4. Inventory    — transfer quantities, rebalancing limits
    5. WebSocket    — broadcast behavior
    6. API          — server host, port, CORS, metadata

Usage:
    from app.config import get_settings
    settings = get_settings()
    print(settings.simulation_tick_interval_seconds)  # 8

Environment override example (any .env file or shell export):
    SIMULATION_TICK_INTERVAL_SECONDS=3
    API_PORT=9000

Design rationale:
    - `pydantic-settings` BaseSettings enables 12-factor-style env-var injection
      without touching code.
    - A module-level `get_settings()` singleton avoids re-parsing on every import
      while remaining testable (call `get_settings.cache_clear()` in tests).
    - Field grouping mirrors the 8-layer engine architecture so that each future
      service module can import only the section it needs via the shared instance.
    - `frozen = True` prevents accidental mutation of settings at runtime.
"""

from functools import lru_cache
from typing import List, Tuple

from pydantic import Field
from pydantic_settings import BaseSettings


# ---------------------------------------------------------------------------
# Simulation Settings
# ---------------------------------------------------------------------------
# Controls the core digital-twin loop: how often the world ticks, how demand
# lambdas are scaled to per-tick values, and the list of product categories
# that drive the entire inventory model.
# ---------------------------------------------------------------------------

class SimulationSettings(BaseSettings):
    """Parameters governing the real-time simulation loop.

    Sources in current codebase:
        main.py:148  — asyncio.sleep(8)
        main.py:66   — prod_data["lambda"] / 30.0
        main.py:57   — product names iterated
    """

    tick_interval_seconds: float = Field(
        default=8.0,
        gt=0,
        description=(
            "Seconds between each simulation tick.  The current system uses 8s. "
            "Lower values increase responsiveness but raise CPU load linearly."
        ),
    )

    lambda_tick_divisor: float = Field(
        default=30.0,
        gt=0,
        description=(
            "Divisor applied to each product's hourly lambda to obtain the "
            "per-tick Poisson rate.  With an 8-second tick, 30.0 means each "
            "tick represents ~16 seconds of simulated real time. "
            "Source: main.py line 66 — tick_lambda = lambda / 30.0"
        ),
    )

    product_categories: List[str] = Field(
        default=["Match Day Snacks", "Daily Essentials", "Cold Beverages"],
        min_length=1,
        description=(
            "Ordered list of product categories tracked across all stores. "
            "The simulation, risk engine, routing, and agentic layers all "
            "iterate over this list.  Changing it here propagates everywhere."
        ),
    )

    model_config = {"env_prefix": "SIMULATION_", "frozen": True}


# ---------------------------------------------------------------------------
# Routing Settings
# ---------------------------------------------------------------------------
# Configures the Google OR-Tools VRP-TW solver: distance scaling, time
# budgets, capacity slack, and the first-solution heuristic.
# ---------------------------------------------------------------------------

class RoutingSettings(BaseSettings):
    """Parameters for the OR-Tools VRP-TW solver.

    Sources in current codebase:
        router.py:27  — distance * 10 (scale factor)
        router.py:65  — 0 (capacity slack)
        router.py:80  — 30 (max waiting time)
        router.py:81  — 300 (max time per vehicle)
        router.py:94  — PATH_CHEAPEST_ARC
    """

    distance_scale_factor: int = Field(
        default=10,
        gt=0,
        description=(
            "Multiplier applied to Euclidean coordinate distances to produce "
            "integer costs for OR-Tools.  Higher values increase solver "
            "precision but may slow convergence.  "
            "Source: router.py line 27"
        ),
    )

    capacity_slack: int = Field(
        default=0,
        ge=0,
        description=(
            "Slack allowed on the capacity dimension.  Zero means vehicles "
            "cannot exceed their stated maximum.  "
            "Source: router.py line 65"
        ),
    )

    max_waiting_time: int = Field(
        default=30,
        ge=0,
        description=(
            "Maximum time (in solver units) a vehicle may wait idle at a node "
            "before the next window opens.  "
            "Source: router.py line 80"
        ),
    )

    max_time_per_vehicle: int = Field(
        default=300,
        gt=0,
        description=(
            "Upper bound on cumulative travel+service time for any single "
            "vehicle route, in solver units.  Routes exceeding this are "
            "infeasible.  "
            "Source: router.py line 81"
        ),
    )

    first_solution_strategy: str = Field(
        default="PATH_CHEAPEST_ARC",
        description=(
            "OR-Tools FirstSolutionStrategy enum name.  Common alternatives: "
            "PARALLEL_CHEAPEST_INSERTION, LOCAL_CHEAPEST_INSERTION, "
            "CHRISTOFIDES.  "
            "Source: router.py line 94"
        ),
    )

    # --- Time-window defaults used when building the data model in main.py ---

    driver_time_window: Tuple[int, int] = Field(
        default=(0, 1000),
        description=(
            "Default (earliest, latest) time window for driver start nodes. "
            "Source: main.py line 103"
        ),
    )

    source_time_window: Tuple[int, int] = Field(
        default=(0, 50),
        description=(
            "Time window for source (pickup) nodes.  "
            "Source: main.py line 108"
        ),
    )

    sink_time_window: Tuple[int, int] = Field(
        default=(0, 30),
        description=(
            "Time window for sink (delivery) nodes — tighter than source to "
            "reflect urgency.  "
            "Source: main.py line 112"
        ),
    )

    pickup_demand_units: int = Field(
        default=20,
        gt=0,
        description=(
            "Units of inventory loaded at each source node.  "
            "Source: main.py line 107"
        ),
    )

    dropoff_demand_units: int = Field(
        default=20,
        gt=0,
        description=(
            "Units of inventory delivered to each sink node.  Stored as a "
            "positive value; the solver receives it negated.  "
            "Source: main.py line 111 (value: -20)"
        ),
    )

    min_route_stops: int = Field(
        default=2,
        ge=1,
        description=(
            "Minimum number of stops (excluding depot) for a route to be "
            "considered actionable.  Routes with fewer stops are discarded.  "
            "Source: main.py line 120 — len(route) > 2"
        ),
    )

    model_config = {"env_prefix": "ROUTING_", "frozen": True}


# ---------------------------------------------------------------------------
# Risk Threshold Settings
# ---------------------------------------------------------------------------
# Numeric thresholds that govern how the predictive layer classifies each
# store-product node into one of: HEALTHY, WARNING, SINK, SOURCE, CRITICAL.
# ---------------------------------------------------------------------------

class RiskSettings(BaseSettings):
    """Thresholds for node risk classification.

    Sources in current codebase:
        main.py:42            — DemandPredictor(critical_threshold=0.80)
        main.py:75            — prob >= 0.80  (SINK)
        main.py:78            — prob >= 0.50 and prob < 0.80  (ghost band)
        predictive_layer.py:48 — stock > lambda * 3 and prob < 0.1  (SOURCE)
    """

    critical_threshold: float = Field(
        default=0.80,
        ge=0.0,
        le=1.0,
        description=(
            "Stockout probability at or above which a node is classified as "
            "SINK (critically low inventory).  Passed to DemandPredictor and "
            "used in the simulation loop.  "
            "Source: main.py line 42 and line 75"
        ),
    )

    ghost_route_lower_bound: float = Field(
        default=0.50,
        ge=0.0,
        le=1.0,
        description=(
            "Lower bound of the probability band that triggers ghost "
            "(anticipatory) route generation.  Nodes with stockout_prob "
            "in [ghost_lower, critical_threshold) receive ghost routes.  "
            "Source: main.py line 78"
        ),
    )

    source_stock_multiplier: float = Field(
        default=3.0,
        gt=0,
        description=(
            "A node qualifies as SOURCE when its stock exceeds "
            "lambda_rate * this multiplier AND its stockout probability is "
            "below source_risk_ceiling.  "
            "Source: predictive_layer.py line 48 — stock > lambda * 3"
        ),
    )

    source_risk_ceiling: float = Field(
        default=0.10,
        ge=0.0,
        le=1.0,
        description=(
            "Maximum allowed stockout probability for a node to qualify as "
            "SOURCE.  Ensures we only redistribute from genuinely safe nodes.  "
            "Source: predictive_layer.py line 48 — prob < 0.1"
        ),
    )

    model_config = {"env_prefix": "RISK_", "frozen": True}


# ---------------------------------------------------------------------------
# Inventory Transfer Settings
# ---------------------------------------------------------------------------
# Governs how much inventory moves, how many nodes participate per tick,
# and how many ghost routes are pre-computed.
# ---------------------------------------------------------------------------

class InventorySettings(BaseSettings):
    """Inventory rebalancing parameters.

    Sources in current codebase:
        main.py:128-129 — stock ± 15
        main.py:105     — sources[:3]
        main.py:109     — sinks[:3]
        main.py:135     — ghosts[:2]
    """

    transfer_quantity: int = Field(
        default=15,
        gt=0,
        description=(
            "Number of units transferred from a source to a sink in each "
            "simulated delivery.  Deducted from source, added to sink.  "
            "Source: main.py lines 128–129"
        ),
    )

    max_sources_per_product: int = Field(
        default=3,
        ge=1,
        description=(
            "Maximum number of source nodes selected per product per tick.  "
            "Limits route complexity and prevents over-routing.  "
            "Source: main.py line 105"
        ),
    )

    max_sinks_per_product: int = Field(
        default=3,
        ge=1,
        description=(
            "Maximum number of sink nodes selected per product per tick.  "
            "Source: main.py line 109"
        ),
    )

    max_ghost_routes_per_product: int = Field(
        default=2,
        ge=0,
        description=(
            "Maximum anticipatory (ghost) routes generated per product per "
            "tick.  Ghost routes warn of impending stockouts before they "
            "breach the critical threshold.  "
            "Source: main.py line 135"
        ),
    )

    model_config = {"env_prefix": "INVENTORY_", "frozen": True}


# ---------------------------------------------------------------------------
# WebSocket Settings
# ---------------------------------------------------------------------------
# Controls the real-time broadcast channel between backend and dashboard.
# ---------------------------------------------------------------------------

class WebSocketSettings(BaseSettings):
    """WebSocket broadcast configuration.

    Sources in current codebase:
        agentic_layer.py:11 — log_history > 50
    """

    max_insight_log_entries: int = Field(
        default=50,
        ge=1,
        description=(
            "Maximum number of AI supervisor log entries retained in memory "
            "and broadcast to clients.  Oldest entries are evicted first.  "
            "Source: agentic_layer.py line 11"
        ),
    )

    model_config = {"env_prefix": "WS_", "frozen": True}


# ---------------------------------------------------------------------------
# API / Server Settings
# ---------------------------------------------------------------------------
# FastAPI application metadata and Uvicorn server bind parameters.
# ---------------------------------------------------------------------------

class APISettings(BaseSettings):
    """FastAPI and Uvicorn server configuration.

    Sources in current codebase:
        main.py:10  — FastAPI(title=...)
        main.py:169 — uvicorn.run(host, port, reload)
    """

    title: str = Field(
        default="Dark Store Optimizer API",
        description="OpenAPI title shown in /docs.",
    )

    version: str = Field(
        default="1.0.0",
        description="Semantic version exposed in the OpenAPI spec.",
    )

    host: str = Field(
        default="0.0.0.0",
        description="Uvicorn bind address.  Source: main.py line 169",
    )

    port: int = Field(
        default=8000,
        ge=1,
        le=65535,
        description="Uvicorn bind port.  Source: main.py line 169",
    )

    reload: bool = Field(
        default=True,
        description="Enable Uvicorn hot-reload for development.",
    )

    cors_origins: List[str] = Field(
        default=["http://localhost:5173", "http://localhost:3000"],
        description=(
            "Allowed CORS origins.  The Vite dev server runs on 5173 by "
            "default; 3000 is included for alternative setups."
        ),
    )

    model_config = {"env_prefix": "API_", "frozen": True}


# ---------------------------------------------------------------------------
# Aggregate Settings
# ---------------------------------------------------------------------------
# Single entry-point that composes all sub-settings.  Every service imports
# this and reads only the fields it needs, keeping coupling minimal.
# ---------------------------------------------------------------------------

class Settings(BaseSettings):
    """Root settings container.

    Composes all sub-settings into a single frozen object.
    Use `get_settings()` to obtain the cached singleton.
    """

    simulation: SimulationSettings = Field(default_factory=SimulationSettings)
    routing: RoutingSettings = Field(default_factory=RoutingSettings)
    risk: RiskSettings = Field(default_factory=RiskSettings)
    inventory: InventorySettings = Field(default_factory=InventorySettings)
    websocket: WebSocketSettings = Field(default_factory=WebSocketSettings)
    api: APISettings = Field(default_factory=APISettings)

    model_config = {"frozen": True}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the application settings singleton.

    Cached via `lru_cache` so the settings object is built exactly once per
    process.  In tests, call `get_settings.cache_clear()` before overriding
    environment variables to force a fresh parse.
    """
    return Settings()
