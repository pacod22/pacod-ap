"""Template-driven edges + auto layout for Phase-1 (template) graphs.

The server owns the edges of a template graph: whatever the client sends, they
are rebuilt from the slot order so the stored graph is always a faithful
description of the pipeline the runtime executes.
"""

from __future__ import annotations

from app.agent_editor.registry import TEMPLATES

ROW_HEIGHT = 120
TOOL_X_STEP = 230
TOOL_X_OFFSET = 300


def _slot_index(template, node_type: str) -> int:
    for i, slot in enumerate(template.slots):
        if node_type in slot.allowed:
            return i
    return len(template.slots)


def normalize_edges(graph: dict) -> dict:
    """Rebuild ``edges`` (and order ``nodes``) from the template slot order."""
    template = TEMPLATES.get(graph.get("template") or "")
    if template is None:
        return graph
    nodes = sorted(graph.get("nodes") or [], key=lambda n: _slot_index(template, n.get("type", "")))
    tool_types = (
        set(next(s.allowed for s in template.slots if s.id == template.tool_slot))
        if template.tool_slot
        else set()
    )
    chain = [n for n in nodes if n.get("type") not in tool_types]
    tools = [n for n in nodes if n.get("type") in tool_types]
    edges: list[dict] = []
    for a, b in zip(chain, chain[1:], strict=False):
        edges.append(
            {"id": f"e:{a['id']}:{b['id']}", "source": a["id"], "target": b["id"], "kind": "flow"}
        )
    if tools and template.tool_parent:
        parent_types = set(next(s.allowed for s in template.slots if s.id == template.tool_parent))
        parent = next((n for n in chain if n.get("type") in parent_types), None)
        # Planner absent → the tools hang off the node before the synthesizer.
        if parent is None:
            synth_idx = next(
                (i for i, n in enumerate(chain) if n.get("type", "").startswith("generate.")), 0
            )
            parent = chain[max(synth_idx - 1, 0)] if chain else None
        if parent is not None:
            for t in tools:
                edges.append(
                    {
                        "id": f"t:{parent['id']}:{t['id']}",
                        "source": parent["id"],
                        "target": t["id"],
                        "kind": "tool",
                    }
                )
    return {**graph, "nodes": nodes, "edges": edges}


def auto_layout(graph: dict) -> dict:
    """Assign positions: the main chain in one column, tools fanned to the right
    of their parent row."""
    template = TEMPLATES.get(graph.get("template") or "")
    if template is None:
        return graph
    tool_types = (
        set(next(s.allowed for s in template.slots if s.id == template.tool_slot))
        if template.tool_slot
        else set()
    )
    row = 0
    parent_row = 0
    out = []
    tool_i = 0
    parent_types = (
        set(next(s.allowed for s in template.slots if s.id == template.tool_parent))
        if template.tool_parent
        else set()
    )
    for n in graph.get("nodes") or []:
        if n.get("type") in tool_types:
            pos = {"x": TOOL_X_OFFSET + tool_i * TOOL_X_STEP, "y": parent_row * ROW_HEIGHT}
            tool_i += 1
        else:
            if n.get("type") in parent_types:
                parent_row = row
            pos = {"x": 0, "y": row * ROW_HEIGHT}
            row += 1
        out.append({**n, "position": pos})
    return {**graph, "nodes": out}
