"""Markdown renderings of the graph, shared by the MCP server and the CLI / hooks."""

from __future__ import annotations

from .model import Node
from .neo4j_store import Neo4jStore


def brief(n: dict | Node) -> str:
    d = n.to_dict() if isinstance(n, Node) else n
    extra = f" [{d['status']}]" if d.get("status") and d["status"] != "active" else ""
    summary = f" — {d['summary']}" if d.get("summary") else ""
    return f"- **{d['id']}** ({d['type']}){extra}: {d['title']}{summary}"


def render_node(store: Neo4jStore, node_id: str, depth: int = 1) -> str:
    node = store.get(node_id)
    if node is None:
        return f"Node `{node_id}` not found. Use search_graph to find the right id."

    out = [f"# {node.title}  (`{node.id}`, {node.type})"]
    meta = [f"status: {node.status}", f"updated: {node.updated}", f"created: {node.created}"]
    if node.path:
        meta.append(f"path: {node.path}")
    if node.tags:
        meta.append("tags: " + ", ".join(node.tags))
    meta.append("projects: " + (", ".join(node.projects) if node.projects else "global"))
    out.append(" | ".join(meta))
    if node.summary:
        out.append(f"\n{node.summary}")
    if node.body:
        out.append(f"\n{node.body}")

    outgoing, incoming = store.neighbors(node_id)
    if outgoing:
        out.append("\n## Outgoing")
        for r in outgoing:
            note = f" — {r['note']}" if r["note"] else ""
            o = r["other"]
            out.append(f"- {r['rel']} → **{o['id']}**: {o['title']} ({o['type']}){note}")
    if incoming:
        out.append("\n## Incoming")
        for r in incoming:
            note = f" — {r['note']}" if r["note"] else ""
            o = r["other"]
            out.append(f"- **{o['id']}**: {o['title']} ({o['type']}) —{r['rel']}→ this{note}")

    if node.type == "project":
        members = store.find(project=node.id, include_global=False, status=None,
                             order="updated", limit=40)
        if members:
            out.append("\n## In this project (most recent first)")
            out.extend(brief(m) for m in members)

    if depth >= 2:
        seen = {node.id}
        extra = []
        for r in outgoing + incoming:
            oid = r["other"]["id"]
            if oid in seen:
                continue
            seen.add(oid)
            o_out, o_in = store.neighbors(oid)
            for rr in o_out + o_in:
                if rr["other"]["id"] not in seen:
                    seen.add(rr["other"]["id"])
                    extra.append(f"- via {oid}: " + brief(rr["other"])[2:])
        if extra:
            out.append("\n## Second-degree neighbors")
            out.extend(extra[:40])
    return "\n".join(out)


def render_overview(store: Neo4jStore, project: str | None = None, recent: int = 8) -> str:
    counts = store.counts()
    if not counts:
        return "The context graph is empty."
    total = sum(counts.values())
    out = [f"# Context graph: {total} nodes (" + ", ".join(f"{t} {c}" for t, c in counts.items()) + ")"]

    def section(heading: str, items: list[dict], limit: int = 25) -> None:
        if items:
            out.append(f"\n## {heading}")
            out.extend(brief(n) for n in items[:limit])
            if len(items) > limit:
                out.append(f"- …and more (search_graph to find them)")

    section("People", store.find(type="person", project=project, limit=26))
    section("Preferences (follow these)", store.find(type="preference", project=project, limit=26))
    section("Projects", store.find(type="project", order="updated", limit=11), limit=10)
    if project:
        section(f"Open tasks in {project}", store.find(type="task", project=project,
                                                       include_global=False, order="updated", limit=16), 15)
        section(f"Decisions in {project}", store.find(type="decision", project=project,
                                                      include_global=False, order="updated", limit=16), 15)
        section(f"Recent sessions in {project}", store.find(type="session", project=project,
                                                            include_global=False, status=None,
                                                            order="updated", limit=3))
    else:
        section("Open tasks", store.find(type="task", order="updated", limit=16), 15)
    section("Recently updated", store.find(status=None, order="updated", limit=recent))
    return "\n".join(out)


def render_project_context(store: Neo4jStore, directory: str) -> str:
    projects = store.projects_for_path(directory)
    if not projects:
        return ""
    return "\n\n".join(render_node(store, p.id) for p in projects[:2])
