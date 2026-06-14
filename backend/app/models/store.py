"""
Store Domain Models — Dynamic Multi-Modal Dark Store Mesh Network Optimizer

Typed Pydantic v2 models that capture the *current* runtime data structures
used across the simulation loop, the WebSocket broadcast, and the React
frontend.  Every field name, type, and default was traced from the live code
paths enumerated below.

Data-flow the models must be compatible with:

    ┌──────────────────────────────────────────────────────────────────┐
    │  STORES dict   (main.py:20-32)                                 │
    │    key: "Store_Indiranagar"                                    │
    │    val: {"coords": [lat,lng],                                  │
    │          "products": {"Match Day Snacks": {"stock":45,"lambda":12}, ...}} │
    └────────────────────┬─────────────────────────────────────────────┘
                         │  simulation tick (main.py:61-88)
                         ▼
    ┌──────────────────────────────────────────────────────────────────┐
    │  nodes_status dict   (main.py:83-88)                           │
    │    key: "Store_Indiranagar"                                    │
    │    val: {"status": "SINK",                                     │
    │          "products": {... same ref as above ...},              │
    │          "coords": [lat,lng],                                  │
    │          "stockout_prob": 0.85}                                │
    └────────────────────┬─────────────────────────────────────────────┘
                         │  json.dumps  (main.py:48)
                         ▼
    ┌──────────────────────────────────────────────────────────────────┐
    │  WebSocket JSON  — consumed by React frontend                  │
    │  Frontend reads:                                               │
    │    data.status            (App.jsx:85,93,122)                  │
    │    data.coords            (App.jsx:122)                        │
    │    data.stockout_prob     (App.jsx:88,92,93,125)               │
    │    data.products[name].stock   (App.jsx:178)                   │
    │    data.products[name].lambda  (App.jsx:41,42,182)             │
    └──────────────────────────────────────────────────────────────────┘

Design principles:
    1.  Internal field names use clean Python identifiers
        (`lambda_rate` not `lambda`, `stockout_probability` not `stockout_prob`).
    2.  Pydantic aliases + `by_alias=True` serialization reproduces the exact
        wire format the frontend expects — zero frontend changes required.
    3.  Factory classmethods accept the raw dicts that main.py already builds,
        so adoption is a one-line change per call site when the twin service
        migrates to typed state.
    4.  Models are strict-mode: no implicit coercion, no extra fields silently
        accepted.
"""

from __future__ import annotations

from enum import Enum
from typing import Dict, List, Tuple

from pydantic import BaseModel, ConfigDict, Field, model_validator


# ---------------------------------------------------------------------------
# StoreStatus Enum
# ---------------------------------------------------------------------------
# Mirrors the three string literals used in the simulation loop.
#
# Sources:
#   main.py:62    — store_status = "HEALTHY"
#   main.py:76    — store_status = "SINK"
#   main.py:80-81 — eval_res["status"] == "SOURCE"
#   predictive_layer.py:45-49  — returns "HEALTHY", "SINK", or "SOURCE"
# ---------------------------------------------------------------------------

class StoreStatus(str, Enum):
    """Operational classification of a store node.

    Inherits from ``str`` so that ``StoreStatus.SINK.value`` is the plain
    string ``"SINK"`` — matching the raw strings already flowing through
    the WebSocket and consumed by the frontend's equality checks
    (``data.status === 'SINK'`` in App.jsx:85).
    """

    HEALTHY = "HEALTHY"
    """Stockout probability is below the ghost-route threshold.
    The store is operating within safe margins for all products."""

    SINK = "SINK"
    """At least one product's stockout probability has breached the
    critical threshold (default 0.80).  Requires immediate inventory
    inflow via active routing."""

    SOURCE = "SOURCE"
    """Stock exceeds ``lambda_rate * source_stock_multiplier`` AND
    stockout probability is below ``source_risk_ceiling``.  This node
    is a candidate to donate inventory to SINK nodes."""


# ---------------------------------------------------------------------------
# ProductInventory Model
# ---------------------------------------------------------------------------
# Represents one product's inventory state at a single store.
#
# Current in-memory shape (main.py:14-18, create_products):
#   {"stock": 45, "lambda": 12}
#
# The dict is keyed by product name ("Match Day Snacks"), so `name` is
# the key in the parent dict — not a field inside the value.  We elevate
# `name` into the model for self-describing objects while keeping wire
# serialization compatible via `to_ws_product_entry()`.
#
# Frontend reads (App.jsx):
#   prod.stock    (line 178)
#   prod.lambda   (lines 41, 42, 182)
# ---------------------------------------------------------------------------

class ProductInventory(BaseModel):
    """Inventory state for a single product at a single store.

    Attributes:
        name:  Product category name.  In the current system this is one of
               ``"Match Day Snacks"``, ``"Daily Essentials"``, or
               ``"Cold Beverages"``.  Matches the dict key in the parent
               ``products`` mapping.
        stock: Current on-hand inventory units.  Decremented each tick by
               Poisson-sampled orders (main.py:69).  Must be ≥ 0.
        lambda_rate:
               Expected demand rate (orders per hour) for this product at
               this store.  Used as the λ parameter for the Poisson process.
               Named ``lambda_rate`` in Python to avoid shadowing the
               ``lambda`` keyword; serialized as ``"lambda"`` on the wire
               via the alias to match frontend expectations
               (``prod.lambda`` in App.jsx:41,42,182).
    """

    model_config = ConfigDict(
        populate_by_name=True,  # Accept both "lambda_rate" and "lambda"
        strict=True,            # No implicit int→float coercion etc.
    )

    name: str = Field(
        ...,
        min_length=1,
        description=(
            "Product category identifier.  "
            "Source: dict key in main.py:14-17 (create_products)"
        ),
        examples=["Match Day Snacks", "Daily Essentials", "Cold Beverages"],
    )

    stock: int = Field(
        ...,
        ge=0,
        description=(
            "Current inventory units on hand.  "
            "Source: main.py:14-17 — create_products() first positional args; "
            "mutated at main.py:69 — max(0, stock - orders)"
        ),
    )

    lambda_rate: float = Field(
        ...,
        gt=0,
        alias="lambda",
        description=(
            "Expected demand rate (Poisson λ) in orders per hour.  "
            "Serializes as 'lambda' for wire compatibility.  "
            "Source: main.py:14-17 — create_products() second positional args; "
            "read at main.py:66 — prod_data['lambda'] / 30.0"
        ),
    )

    # -- Factory helpers --------------------------------------------------

    @classmethod
    def from_runtime(cls, name: str, raw: Dict) -> ProductInventory:
        """Construct from the current in-memory product dict.

        Args:
            name: Product name (the dict key, e.g. ``"Match Day Snacks"``).
            raw:  The value dict, e.g. ``{"stock": 45, "lambda": 12}``.

        Example::

            ProductInventory.from_runtime(
                "Match Day Snacks",
                {"stock": 45, "lambda": 12}
            )
        """
        return cls(name=name, stock=raw["stock"], lambda_rate=raw["lambda"])

    # -- Serialization helpers --------------------------------------------

    def to_ws_dict(self) -> Dict:
        """Serialize to the exact shape the frontend expects inside
        ``products[name]``.

        Returns::

            {"stock": 45, "lambda": 12}

        This is the *value* portion only.  The caller uses ``self.name``
        as the dict *key*.
        """
        return {
            "stock": self.stock,
            "lambda": self.lambda_rate,
        }


# ---------------------------------------------------------------------------
# Store Model
# ---------------------------------------------------------------------------
# Represents a single dark-store node's complete state as it flows through
# the simulation and onto the wire.
#
# Current in-memory shape (main.py:83-88, nodes_status):
#   {
#       "status":       "SINK",
#       "products":     { "Match Day Snacks": {"stock":45,"lambda":12}, ... },
#       "coords":       [12.9784, 77.6408],
#       "stockout_prob": 0.85
#   }
#
# The dict is keyed by store_id ("Store_Indiranagar").
#
# Frontend reads (App.jsx):
#   data.status         (lines 85, 93, 122)
#   data.coords         (line 122 — Leaflet Marker position)
#   data.stockout_prob   (lines 88, 92, 93, 125)
#   data.products[name]  (lines 38-39, 160, 178, 182)
# ---------------------------------------------------------------------------

class Store(BaseModel):
    """Complete state of a single dark-store node.

    Attributes:
        id:     Unique store identifier.  Used as the dict key in the
                ``stores`` payload and displayed in the frontend sidebar
                after stripping the ``Store_`` prefix (App.jsx:86).
        coords: Geographic position as ``(latitude, longitude)``.
                Serialized as a JSON array for Leaflet ``position`` prop
                (App.jsx:122).
        status: Current operational classification derived from the
                highest per-product stockout probability at this node.
        products:
                Per-product inventory state, keyed by product name.
                The ``ProductInventory.name`` field always matches its
                dict key for internal consistency.
        stockout_probability:
                Maximum stockout probability across all products at this
                store.  Named ``stockout_probability`` internally for
                readability; serialized as ``stockout_prob`` via alias
                to match the wire format consumed by the frontend
                (``data.stockout_prob`` in App.jsx:88).
    """

    model_config = ConfigDict(
        populate_by_name=True,  # Accept both Python names and aliases
    )

    id: str = Field(
        ...,
        min_length=1,
        description=(
            "Unique store identifier (e.g. 'Store_Indiranagar').  "
            "Source: dict key in main.py:20-32 (STORES)"
        ),
        examples=["Store_Indiranagar", "Store_Koramangala"],
    )

    coords: Tuple[float, float] = Field(
        ...,
        description=(
            "Geographic location as (latitude, longitude).  "
            "Serialized as a 2-element JSON array for Leaflet.  "
            "Source: main.py:21 — [12.9784, 77.6408]"
        ),
    )

    status: StoreStatus = Field(
        default=StoreStatus.HEALTHY,
        description=(
            "Operational classification.  Defaults to HEALTHY; promoted to "
            "SINK when any product crosses the critical threshold, or to "
            "SOURCE when the predictive layer flags excess inventory.  "
            "Source: main.py:62 (default), main.py:76 (SINK), main.py:80 (SOURCE)"
        ),
    )

    products: Dict[str, ProductInventory] = Field(
        ...,
        min_length=1,
        description=(
            "Per-product inventory state, keyed by product category name.  "
            "Source: main.py:85 — data['products']"
        ),
    )

    stockout_probability: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        alias="stockout_prob",
        description=(
            "Maximum stockout probability across all products at this store.  "
            "Serializes as 'stockout_prob' for wire compatibility.  "
            "Source: main.py:87 — max_prob across all products"
        ),
    )

    # -- Validation -------------------------------------------------------

    @model_validator(mode="after")
    def _product_names_match_keys(self) -> Store:
        """Ensure every ProductInventory.name matches its dict key.

        This catches construction mistakes where a product is inserted
        under the wrong key, which would silently corrupt the wire payload.
        """
        for key, product in self.products.items():
            if product.name != key:
                raise ValueError(
                    f"Product key '{key}' does not match product.name "
                    f"'{product.name}'.  These must be identical."
                )
        return self

    # -- Factory helpers --------------------------------------------------

    @classmethod
    def from_runtime(cls, store_id: str, data: Dict) -> Store:
        """Construct a Store from the raw dicts currently used at runtime.

        Accepts **either** of the two dict shapes produced by main.py:

        1. **STORES entry** (pre-simulation, main.py:20-32)::

               {"coords": [12.97, 77.64],
                "products": {"Match Day Snacks": {"stock": 45, "lambda": 12}, ...}}

        2. **nodes_status entry** (post-tick, main.py:83-88)::

               {"status": "SINK",
                "products": {"Match Day Snacks": {"stock": 30, "lambda": 12}, ...},
                "coords": [12.97, 77.64],
                "stockout_prob": 0.85}

        Args:
            store_id: The dict key (e.g. ``"Store_Indiranagar"``).
            data:     The value dict in either shape above.

        Returns:
            A fully validated ``Store`` instance.
        """
        products = {
            prod_name: ProductInventory.from_runtime(prod_name, prod_data)
            for prod_name, prod_data in data["products"].items()
        }

        return cls(
            id=store_id,
            coords=tuple(data["coords"]),
            status=StoreStatus(data.get("status", "HEALTHY")),
            products=products,
            stockout_probability=data.get("stockout_prob", 0.0),
        )

    # -- Serialization helpers --------------------------------------------

    def to_ws_payload(self) -> Dict:
        """Serialize to the exact JSON-ready dict the frontend expects.

        Returns the *value* portion of the ``stores`` payload.  The caller
        uses ``self.id`` as the dict *key*.

        Output shape::

            {
                "status": "SINK",
                "products": {
                    "Match Day Snacks": {"stock": 30, "lambda": 12},
                    ...
                },
                "coords": [12.9784, 77.6408],
                "stockout_prob": 0.85
            }

        This matches:
            - main.py:83-88 (nodes_status construction)
            - main.py:158-159 (initial WS handshake)
            - App.jsx field accesses (lines 41,42,85,88,92,93,122,125,178,182)
        """
        return {
            "status": self.status.value,
            "products": {
                name: product.to_ws_dict()
                for name, product in self.products.items()
            },
            "coords": list(self.coords),
            "stockout_prob": self.stockout_probability,
        }

    def get_product(self, product_name: str) -> ProductInventory:
        """Retrieve a product by name with a clear error on miss.

        Args:
            product_name: Exact product category name.

        Returns:
            The ``ProductInventory`` for that product.

        Raises:
            KeyError: If the product does not exist at this store.
        """
        try:
            return self.products[product_name]
        except KeyError:
            raise KeyError(
                f"Product '{product_name}' not found at store '{self.id}'.  "
                f"Available: {list(self.products.keys())}"
            )

    @property
    def is_critical(self) -> bool:
        """Whether this store is currently classified as a SINK."""
        return self.status == StoreStatus.SINK

    @property
    def is_source(self) -> bool:
        """Whether this store can donate inventory."""
        return self.status == StoreStatus.SOURCE

    @property
    def product_names(self) -> List[str]:
        """Ordered list of product names at this store."""
        return list(self.products.keys())
