from __future__ import annotations

import datetime as dt

from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel, Field

from .render import line, render_node, render_overview, render_project_context
from .store import NODE_TYPES, SUGGESTED_RELATIONS, GraphError, GraphStore, Relation, slugify

INSTRUCTIONS = f"""\
Persistent context graph shared across every project and session with this user.
Read: graph_overview at the start of work; search_graph / get_node before asking the user
something they may have told you before; context_for_directory for the current repo.
Write as you go, not only at the end: new facts about the user, preferences and corrections,
decisions (with the why), projects, resources, open tasks. Prefer updating an existing node
(search first) over creating near-duplicates. Keep `summary` to one line; details go in `body`.
When a fact changes, update the node (or mark the old decision status=superseded and link
`supersedes`) instead of leaving contradictions. Never store secrets, tokens or passwords.
Node types: {", ".join(sorted(NODE_TYPES))}.
Suggested relations: {", ".join(SUGGESTED_RELATIONS)}.
"""

mcp = MCPServer("context-graph", instructions=INSTRUCTIONS)
store = GraphStore()


class RelationIn(BaseModel):
    type: str = Field(description="snake_case relation, e.g. works_on, depends_on, decided")
    target: str = Field(description="id of the target node (must already exist)")
    note: str | None = Field(default=None, description="optional short qualifier")


def _err(e: Exception) -> str:
    return f"Error: {e}"


@mcp.tool()
def graph_overview() -> str:
    """Compact overview: the user, their preferences, active projects, open tasks, recent changes."""
    return render_overview(store)


@mcp.tool()
def search_graph(query: str, type: str | None = None, limit: int = 10) -> str:
    """Keyword search over node ids, titles, tags, summaries and bodies. Optionally filter by type."""
    hits = store.search(query, type=type, limit=limit)
    if not hits:
        return f"No nodes match {query!r}."
    return "\n".join(line(n) for _, n in hits)


@mcp.tool()
def get_node(id: str, depth: int = 1) -> str:
    """Full node with outgoing and incoming relations. depth=2 also lists neighbor summaries."""
    return render_node(store, id, depth=min(max(depth, 1), 2))


@mcp.tool()
def context_for_directory(path: str) -> str:
    """Project node(s) whose `path` contains this directory, with their relations."""
    return render_project_context(store, path) or (
        f"No project node is registered for {path}. If this is a project worth remembering, "
        "create one with upsert_node(type='project', path=...)."
    )


@mcp.tool()
def upsert_node(
    id: str,
    type: str | None = None,
    title: str | None = None,
    summary: str | None = None,
    body: str | None = None,
    append_body: str | None = None,
    tags: list[str] | None = None,
    relations: list[RelationIn] | None = None,
    path: str | None = None,
    status: str | None = None,
) -> str:
    """Create a node, or merge into an existing one (only the fields you pass change).

    id: lowercase-dash slug, stable and descriptive (e.g. 'pref-concise-answers').
    type: required when creating. summary: one line. body: replaces the markdown body;
    append_body: adds a dated paragraph instead. tags replace existing tags. relations are
    added (deduplicated) as outgoing edges; targets must be valid ids. path: filesystem root
    for project nodes. status: active | done | superseded | archived.
    """
    try:
        rels = [Relation(r.type, r.target, r.note) for r in relations or []]
        missing = [r.target for r in rels if store.get(r.target) is None and r.target != id]
        if missing:
            return _err(f"relation targets do not exist yet: {missing}. Create them first.")
        node, created = store.upsert(
            id, type=type, title=title, summary=summary, body=body, append_body=append_body,
            tags=tags, relations=rels, path=path, status=status,
        )
    except GraphError as e:
        return _err(e)
    return f"{'Created' if created else 'Updated'} `{node.id}` ({node.type})."


@mcp.tool()
def link(source: str, relation: str, target: str, note: str | None = None) -> str:
    """Add a directed relation source -relation-> target between two existing nodes."""
    try:
        added = store.link(source, relation, target, note)
    except GraphError as e:
        return _err(e)
    return f"{'Linked' if added else 'Already linked'}: {source} -{relation}-> {target}"


@mcp.tool()
def unlink(source: str, relation: str, target: str) -> str:
    """Remove a relation."""
    try:
        removed = store.unlink(source, relation, target)
    except GraphError as e:
        return _err(e)
    return "Removed." if removed else "No such relation."


@mcp.tool()
def delete_node(id: str) -> str:
    """Delete a node and all relations pointing to it. Prefer status='archived' or 'superseded'
    for anything with historical value; deletion is for mistakes and duplicates."""
    try:
        edges = store.delete(id)
    except GraphError as e:
        return _err(e)
    return f"Deleted `{id}` and {edges} incoming relation(s)."


@mcp.tool()
def log_session(
    project: str,
    summary: str,
    details: str | None = None,
    related: list[str] | None = None,
) -> str:
    """Record what happened in a working session: a session node linked `about` the project
    and `relates_to` any other node ids given (decisions made, tasks touched, ...)."""
    if store.get(project) is None:
        return _err(f"project node {project!r} does not exist; create it first")
    base = f"session-{dt.date.today().isoformat()}-{slugify(project)}"
    sid, n = base, 2
    while store.get(sid) is not None:
        sid, n = f"{base}-{n}", n + 1
    rels = [Relation("about", project)] + [
        Relation("relates_to", r) for r in related or [] if store.get(r) is not None
    ]
    try:
        store.upsert(sid, type="session", title=f"Session {dt.date.today().isoformat()} · {project}",
                     summary=summary, body=details, relations=rels)
    except GraphError as e:
        return _err(e)
    return f"Logged `{sid}`."


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
