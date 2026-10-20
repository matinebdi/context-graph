"""MCP server exposing the context graph to Claude."""

from __future__ import annotations

import datetime as dt
import json
from functools import wraps

from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel, Field

from .config import project_dir
from .model import DEFAULT_TYPES, GLOBAL_TYPES, SUGGESTED_RELATIONS, GraphError, Relation, slugify
from .neo4j_store import Neo4jStore
from .render import brief, render_node, render_overview, render_project_context

INSTRUCTIONS = f"""\
Persistent context graph (Neo4j) shared across the projects where this plugin is enabled.
It is your long-term memory of the user and their work.

Read: call graph_overview when starting substantial work (the SessionStart hook may already
have injected it). Before asking the user something they may have told you before, use
search_graph / get_node. Use query_cypher for structural questions.

Write as you go, not only at the end: facts about the user, preferences and corrections,
decisions with their reasons, components and concepts of the project, useful resources, open
tasks. Search first and update an existing node rather than creating a near-duplicate. Keep
`summary` to one line; details go in `body` or `append_body`. When something changes, update the
node, or set status='superseded' on the old decision and link the new one with SUPERSEDES.
Never store secrets, tokens, passwords or personal data the user has not chosen to share.

Scope: nodes are attached to the current project automatically, except global types
({", ".join(sorted(GLOBAL_TYPES))}). Override with scope='global' or scope='project'.
Default node types: {"; ".join(f"{k} = {v}" for k, v in DEFAULT_TYPES.items())}.
Custom snake_case types are allowed when none of these fit.
Suggested relations: {", ".join(SUGGESTED_RELATIONS)}.
"""

mcp = MCPServer("context-graph", instructions=INSTRUCTIONS)
store = Neo4jStore()
_project_cache: dict[str, str] = {}


def current_project() -> str:
    """Id of the project node for the session's project directory (created on first use)."""
    directory = project_dir()
    if directory not in _project_cache:
        _project_cache[directory] = store.ensure_project(directory).id
    return _project_cache[directory]


def tool(fn):
    """Register an MCP tool that reports GraphErrors as text instead of failing the call."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except GraphError as e:
            return f"Error: {e}"
    return mcp.tool()(wrapper)


class RelationIn(BaseModel):
    type: str = Field(description="relation type, e.g. WORKS_ON, DEPENDS_ON, DECIDED (case-insensitive)")
    target: str = Field(description="id of the target node (must already exist)")
    note: str | None = Field(default=None, description="optional short qualifier")


@tool
def graph_overview(all_projects: bool = False) -> str:
    """Compact overview: people, preferences, projects, and the current project's open tasks,
    decisions and recent sessions. all_projects=True ignores the current project scope."""
    return render_overview(store, None if all_projects else current_project())


@tool
def search_graph(query: str, type: str | None = None, limit: int = 10) -> str:
    """Full-text search over ids, titles, summaries, bodies and tags. Results from the current
    project rank higher. Optionally filter by node type."""
    hits = store.search(query, type=type, project=current_project(), limit=limit)
    if not hits:
        return f"No nodes match {query!r}."
    return "\n".join(brief(n) for _, n in hits)


@tool
def get_node(id: str, depth: int = 1) -> str:
    """Full node with outgoing and incoming relations (and members, for a project).
    depth=2 also lists second-degree neighbors."""
    return render_node(store, id, depth=min(max(depth, 1), 2))


@tool
def context_for_directory(path: str) -> str:
    """Project node(s) whose `path` contains this directory, with their relations."""
    return render_project_context(store, path) or f"No project node is registered for {path}."


@tool
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
    scope: str = "auto",
) -> str:
    """Create a node, or merge into an existing one (only the fields you pass change).

    id: stable lowercase-dash slug, e.g. 'pref-concise-answers', 'decision-use-postgres'.
    type: required when creating (see the server instructions for the default types).
    summary: one line. body replaces the markdown body; append_body adds a dated paragraph.
    tags replace existing tags. relations are added as outgoing edges to existing nodes.
    path: filesystem root, for project nodes. status: active | done | superseded | archived.
    scope: 'auto' (attach new non-global nodes to the current project), 'project', or 'global'.
    """
    existing_type = None
    if scope == "auto":
        existing = store.get(id)
        existing_type = existing.type if existing else None
    effective_type = (type or existing_type or "").lower()
    attach = scope == "project" or (scope == "auto" and effective_type not in GLOBAL_TYPES)
    node, created = store.upsert(
        id, type=type, title=title, summary=summary, body=body, append_body=append_body, tags=tags,
        relations=[Relation(r.type, r.target, r.note) for r in relations or []], path=path,
        status=status, project=current_project() if attach and id != current_project() else None,
    )
    where = f" in project {', '.join(node.projects)}" if node.projects else " (global)"
    return f"{'Created' if created else 'Updated'} `{node.id}` ({node.type}){where}."


@tool
def link(source: str, relation: str, target: str, note: str | None = None) -> str:
    """Add a directed relation source -[RELATION]-> target between two existing nodes."""
    added = store.link(source, relation, target, note)
    return f"{'Linked' if added else 'Already linked'}: {source} -[{relation.upper()}]-> {target}"


@tool
def unlink(source: str, relation: str, target: str) -> str:
    """Remove a relation."""
    return "Removed." if store.unlink(source, relation, target) else "No such relation."


@tool
def set_node_project(id: str, project: str | None = None, remove: bool = False) -> str:
    """Attach a node to a project (default: the current one), or detach it with remove=True."""
    pid = project or current_project()
    store.set_project(id, pid, remove=remove)
    return f"{'Detached' if remove else 'Attached'} `{id}` {'from' if remove else 'to'} project `{pid}`."


@tool
def delete_node(id: str) -> str:
    """Delete a node and all its relations. Prefer status='archived' or 'superseded' for anything
    with historical value; deletion is for mistakes and duplicates."""
    return f"Deleted `{id}` and {store.delete(id)} incoming relation(s)."


@tool
def log_session(summary: str, details: str | None = None, related: list[str] | None = None) -> str:
    """Record what happened in this working session: a session node in the current project,
    linked ABOUT the project and RELATES_TO the given node ids (decisions, tasks, ...)."""
    project = current_project()
    today = dt.date.today().isoformat()
    base = f"session-{today}-{slugify(project)}"[:80]
    sid, n = base, 2
    while store.get(sid) is not None:
        sid, n = f"{base[:76]}-{n}", n + 1
    rels = [Relation("ABOUT", project)] + [Relation("RELATES_TO", r) for r in related or []]
    store.upsert(sid, type="session", title=f"Session {today} · {project}", summary=summary,
                 body=details, relations=rels, project=project)
    return f"Logged `{sid}`."


@tool
def query_cypher(cypher: str, params: dict | None = None) -> str:
    """Run a read-only Cypher query (writes are rejected by the database). Nodes carry the label
    :Entity plus a type label (:Project, :Decision, ...); project membership is
    (n)-[:IN_PROJECT]->(:Project). Returns up to 100 rows as JSON."""
    rows = store.read_cypher(cypher, params)
    return json.dumps(rows, indent=1, ensure_ascii=False) if rows else "No rows."


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
