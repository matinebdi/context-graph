from __future__ import annotations

from collections import Counter

from .store import GraphStore, Node


def line(node: Node) -> str:
    extra = f" [{node.status}]" if node.status != "active" else ""
    summary = f" — {node.summary}" if node.summary else ""
    return f"- **{node.id}** ({node.type}){extra}: {node.title}{summary}"


def render_node(store: GraphStore, node_id: str, depth: int = 1) -> str:
    nodes = store.load_all()
    node = nodes.get(node_id)
    if node is None:
        return f"Node `{node_id}` not found. Use search_graph to find the right id."

    out = [f"# {node.title}  (`{node.id}`, {node.type})"]
    meta = [f"status: {node.status}", f"updated: {node.updated}", f"created: {node.created}"]
    if node.path:
        meta.append(f"path: {node.path}")
    if node.tags:
        meta.append("tags: " + ", ".join(node.tags))
    out.append(" · ".join(meta))
    if node.summary:
        out.append(f"\n{node.summary}")
    if node.body:
        out.append(f"\n{node.body}")

    def title(nid: str) -> str:
        n = nodes.get(nid)
        return f"{n.title} ({n.type})" if n else "⚠ missing node"

    if node.relations:
        out.append("\n## Outgoing")
        for r in node.relations:
            note = f" — {r.note}" if r.note else ""
            out.append(f"- {r.type} → **{r.target}**: {title(r.target)}{note}")
    incoming = store.incoming(nodes, node.id)
    if incoming:
        out.append("\n## Incoming")
        for src, r in incoming:
            note = f" — {r.note}" if r.note else ""
            out.append(f"- **{src.id}**: {src.title} ({src.type}) —{r.type}→ this{note}")

    if depth >= 2:
        neighbor_ids = {r.target for r in node.relations} | {s.id for s, _ in incoming}
        neighbor_ids.discard(node.id)
        if neighbor_ids:
            out.append("\n## Neighbors")
            for nid in sorted(neighbor_ids):
                if nid in nodes:
                    out.append(line(nodes[nid]))
    return "\n".join(out)


def render_overview(store: GraphStore, recent: int = 8) -> str:
    nodes = store.load_all()
    if not nodes:
        return "The context graph is empty."
    counts = Counter(n.type for n in nodes.values())
    out = [
        f"# Context graph: {len(nodes)} nodes ("
        + ", ".join(f"{t} {c}" for t, c in sorted(counts.items()))
        + ")"
    ]

    def section(heading: str, items: list[Node], limit: int = 25) -> None:
        if items:
            out.append(f"\n## {heading}")
            out.extend(line(n) for n in items[:limit])
            if len(items) > limit:
                out.append(f"- …and {len(items) - limit} more")

    active = [n for n in nodes.values() if n.status == "active"]
    by_type = lambda t: sorted((n for n in active if n.type == t), key=lambda n: n.id)  # noqa: E731
    section("People", by_type("person"))
    section("Preferences (follow these)", by_type("preference"))
    section("Active projects", by_type("project"))
    section("Open tasks", by_type("task"))
    latest = sorted(nodes.values(), key=lambda n: n.updated, reverse=True)[:recent]
    out.append("\n## Recently updated")
    out.extend(f"- {n.id} ({n.type}) — {n.title} · {n.updated[:10]}" for n in latest)
    return "\n".join(out)


def render_project_context(store: GraphStore, cwd: str) -> str:
    projects = store.projects_for_path(cwd)
    if not projects:
        return ""
    return "\n\n".join(render_node(store, p.id, depth=1) for p in projects[:2])
