"""The stored agent graph (``agent_definitions.draft_graph`` / ``agent_versions.graph``).

Schema version 1. Phase 1 graphs follow a *template* (fixed slot order, see
``registry.TEMPLATES``) and the server normalises ``edges`` from that order; the
edges are still stored as data so a later free-form executor (``template: None``)
needs no data migration — only ``migrate_graph`` converters for future versions.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = 1
LOCALES = ("en", "bg", "de", "ru")


class Position(BaseModel):
    x: float = 0.0
    y: float = 0.0


class GraphNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=64)
    type: str
    enabled: bool = True
    position: Position = Field(default_factory=Position)
    # Validated per node type against registry.NodeType.config_model.
    config: dict[str, Any] = Field(default_factory=dict)


class GraphEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    source: str
    target: str
    kind: Literal["flow", "tool"] = "flow"


class MarketingCard(BaseModel):
    name: str = ""
    desc: str = ""
    tag: str | None = None


class Marketing(BaseModel):
    """Public listing (landing page card, /agents/<slug> page, sitemap)."""

    model_config = ConfigDict(extra="forbid")

    slug: str = ""
    show_on_landing: bool = False
    order: int = 100
    accent: str = ""
    soft: str = ""
    icon: str = ""
    card: dict[str, MarketingCard] = Field(default_factory=dict)
    # Per-locale full agent page document (shape of the frontend AgentPage type).
    page: dict[str, dict[str, Any]] = Field(default_factory=dict)


class GraphMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = ""
    description: str = ""
    short_names: dict[str, str] = Field(default_factory=dict)  # locale → label
    legal_year: int | None = None
    marketing: Marketing | None = None


class AgentGraph(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = SCHEMA_VERSION
    template: str | None
    meta: GraphMeta = Field(default_factory=GraphMeta)
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)

    def node_by_type(self, type_: str) -> GraphNode | None:
        return next((n for n in self.nodes if n.type == type_), None)


def migrate_graph(raw: dict) -> dict:
    """Bring a stored graph up to ``SCHEMA_VERSION``. Converters are keyed by the
    version they upgrade *from*; there are none yet."""
    version = int(raw.get("schema_version") or 1)
    if version > SCHEMA_VERSION:
        raise ValueError(f"graph schema_version {version} is newer than supported {SCHEMA_VERSION}")
    return {**raw, "schema_version": SCHEMA_VERSION}


def parse_graph(raw: dict) -> AgentGraph:
    """Raw JSON dict → AgentGraph (after migration). Raises pydantic.ValidationError."""
    return AgentGraph.model_validate(migrate_graph(raw))
