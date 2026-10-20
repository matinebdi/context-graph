import datetime as dt
import json

from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel, Field

from context_graph.config import Environment
from context_graph.domain import GraphError, Relation, Schema
from context_graph.rendering import MarkdownRenderer
from context_graph.storage import GraphMaintenance, Neo4jStore


class RelationInput(BaseModel):
    """A relation as passed to ``upsert_node``."""

    type: str = Field(description="relation type, e.g. WORKS_ON, DEPENDS_ON, DECIDED (case-insensitive)")
    target: str = Field(description="id of the target node (must already exist)")
    note: str | None = Field(default=None, description="optional short qualifier")


class ContextGraphServer:
    """MCP server exposing the context graph as tools.

    Each public tool method is registered with the MCP SDK, which builds the tool's input schema
    from its signature and its description from its docstring. Failures are raised as
    ``GraphError``, which the SDK reports to the client as a tool error with its message.

    Attributes:
        store: Graph store.
        renderer: Markdown views over the store.
        mcp: Underlying MCP SDK server.
    """

    NAME = "context-graph"

    def __init__(self, store: Neo4jStore | None = None):
        self.store = store or Neo4jStore()
        self.renderer = MarkdownRenderer(self.store)
        self.maintenance = GraphMaintenance(self.store)
        self.mcp = MCPServer(self.NAME, instructions=self.instructions())
        self._projects: dict[str, str] = {}
        for tool in (
            self.graph_overview, self.search_graph, self.get_node, self.context_for_directory,
            self.upsert_node, self.link, self.unlink, self.set_node_project, self.delete_node,
            self.merge_nodes, self.log_session, self.review_graph, self.query_cypher,
        ):
            self.mcp.add_tool(tool)

    @staticmethod
    def instructions() -> str:
        """Returns the server instructions sent to clients at initialization."""
        types = "; ".join(f"{k} = {v}" for k, v in Schema.DEFAULT_TYPES.items())
        return f"""\
Persistent context graph (Neo4j) shared across every project where it is enabled.
It is your long-term memory of the user and their work.

Read: call graph_overview when starting substantial work (a session-start hook may already
have injected it). Before asking the user something they may have told you before, use
search_graph / get_node. search_graph understands meaning and works across languages, so
describe what you need in plain words. Use query_cypher for structural questions.

Write as you go, not only at the end: facts about the user, preferences and corrections,
decisions with their reasons, components and concepts of the project, useful resources, open
tasks. Search first and update an existing node rather than creating a near-duplicate. Keep
`summary` to one line; details go in `body` or `append_body`. When something changes, update the
node, or set status='superseded' on the old decision and link the new one with SUPERSEDES.
If upsert_node reports similar existing nodes, prefer updating one of them, or merge_nodes.
Never store secrets, tokens, passwords or personal data the user has not chosen to share.

Scope: nodes are attached to the current project automatically, except global types
({", ".join(sorted(Schema.GLOBAL_TYPES))}). Override with scope='global' or scope='project'.
Default node types: {types}.
Custom snake_case types are allowed when none of these fit.
Suggested relations: {", ".join(Schema.SUGGESTED_RELATIONS)}.
"""

    def current_project(self) -> str | None:
        """Returns the id of the session's project node, registering the project on first use.

        Returns None when the project directory is unknown; tools then work graph-wide.
        """
        directory = Environment.project_dir()
        if not directory:
            return None
        if directory not in self._projects:
            self._projects[directory] = self.store.ensure_project(directory).id
        return self._projects[directory]

    def required_project(self, project: str | None = None) -> str:
        """Returns ``project`` or the current project.

        Raises:
            GraphError: If neither is known.
        """
        resolved = project or self.current_project()
        if not resolved:
            raise GraphError(
                "the current project is unknown (the client didn't say which folder it works in); "
                "pass a project id explicitly, or set CONTEXT_GRAPH_PROJECT_DIR in the MCP config"
            )
        return resolved

    # ------------------------------------------------------------------ tools

    def graph_overview(self, all_projects: bool = False) -> str:
        """Compact overview of the graph: people, preferences, projects, and the current project's
        open tasks, decisions and recent sessions.

        Args:
            all_projects: Ignore the current project scope and summarize the whole graph.
        """
        return self.renderer.overview(None if all_projects else self.current_project())

    def search_graph(self, query: str, type: str | None = None, limit: int = 10) -> str:
        """Hybrid search: matches by meaning (in any language) and by keywords over titles,
        summaries, bodies and tags. Results from the current project rank higher.

        Args:
            query: What you are looking for, in plain words or keywords.
            type: Only return nodes of this type.
            limit: Maximum number of results.
        """
        hits = self.store.search(query, type=type, project=self.current_project(), limit=limit)
        if not hits:
            return f"No nodes match {query!r}."
        self.store.touch([node.id for _, node in hits])
        return "\n".join(self.renderer.brief(node) for _, node in hits)

    def get_node(self, id: str, depth: int = 1) -> str:
        """Full node with its outgoing and incoming relations (and members, for a project).

        Args:
            id: Node id.
            depth: 2 also lists second-degree neighbors.
        """
        self.store.touch([id])
        return self.renderer.node(id, depth=min(max(depth, 1), 2))

    def context_for_directory(self, path: str) -> str:
        """Project node(s) whose `path` contains this directory, with their relations.

        Args:
            path: Filesystem directory.
        """
        return self.renderer.project_context(path) or f"No project node is registered for {path}."

    def upsert_node(
        self,
        id: str,
        type: str | None = None,
        title: str | None = None,
        summary: str | None = None,
        body: str | None = None,
        append_body: str | None = None,
        tags: list[str] | None = None,
        relations: list[RelationInput] | None = None,
        path: str | None = None,
        status: str | None = None,
        scope: str = "auto",
    ) -> str:
        """Create a node, or merge into an existing one (only the fields you pass change).

        Args:
            id: Stable lowercase-dash slug, e.g. 'pref-concise-answers', 'decision-use-postgres'.
            type: Required when creating; see the server instructions for the default types.
            title: Human-readable name.
            summary: One line.
            body: Markdown body; replaces the current one.
            append_body: Paragraph added to the body with today's date.
            tags: Replace the current tags.
            relations: Outgoing relations to existing nodes; added, never removed.
            path: Filesystem root, for project nodes.
            status: active | done | superseded | archived.
            scope: 'auto' attaches new non-global nodes to the current project; 'project' always
                attaches; 'global' never does.
        """
        existing_type = None
        if scope == "auto":
            existing = self.store.get(id)
            existing_type = existing.type if existing else None
        effective_type = (type or existing_type or "").lower()
        attach = scope == "project" or (scope == "auto" and effective_type not in Schema.GLOBAL_TYPES)
        project = self.current_project() if attach else None
        node, created = self.store.upsert(
            id, type=type, title=title, summary=summary, body=body, append_body=append_body,
            tags=tags, relations=[Relation(r.type, r.target, r.note) for r in relations or []],
            path=path, status=status, project=project if project != id else None,
        )
        where = f" in project {', '.join(node.projects)}" if node.projects else " (global)"
        reply = f"{'Created' if created else 'Updated'} `{node.id}` ({node.type}){where}."
        if created:
            similar = self.store.similar(self.store.text_of(node), type=node.type, exclude=node.id)
            if similar:
                listed = ", ".join(f"`{brief['id']}` ({score:.2f})" for score, brief in similar)
                reply += (f" Similar existing {node.type} nodes: {listed}. If one is the same thing, "
                          f"merge them with merge_nodes(keep, remove).")
        return reply

    def link(self, source: str, relation: str, target: str, note: str | None = None) -> str:
        """Add a directed relation source -[RELATION]-> target between two existing nodes.

        Args:
            source: Source node id.
            relation: Relation type, e.g. DEPENDS_ON.
            target: Target node id.
            note: Optional short qualifier.
        """
        added = self.store.link(source, relation, target, note)
        return f"{'Linked' if added else 'Already linked'}: {source} -[{relation.upper()}]-> {target}"

    def unlink(self, source: str, relation: str, target: str) -> str:
        """Remove a relation.

        Args:
            source: Source node id.
            relation: Relation type.
            target: Target node id.
        """
        return "Removed." if self.store.unlink(source, relation, target) else "No such relation."

    def set_node_project(self, id: str, project: str | None = None, remove: bool = False) -> str:
        """Attach a node to a project, or detach it.

        Args:
            id: Node id.
            project: Project id; defaults to the current project.
            remove: Detach instead of attach.
        """
        project_id = self.required_project(project)
        self.store.set_project(id, project_id, remove=remove)
        action = "Detached" if remove else "Attached"
        return f"{action} `{id}` {'from' if remove else 'to'} project `{project_id}`."

    def delete_node(self, id: str) -> str:
        """Delete a node and all its relations. Prefer status='archived' or 'superseded' for
        anything with historical value; deletion is for mistakes and duplicates.

        Args:
            id: Node id.
        """
        return f"Deleted `{id}` and {self.store.delete(id)} incoming relation(s)."

    def merge_nodes(self, keep: str, remove: str) -> str:
        """Merge a duplicate into another node: relations, project links, tags and text move to
        `keep`, then `remove` is deleted.

        Args:
            keep: Id of the node that survives.
            remove: Id of the duplicate to fold into it.
        """
        return self.maintenance.merge(keep, remove)

    def review_graph(self, stale_days: int = 30) -> str:
        """Check the graph's health: likely duplicates, orphan nodes, stale open tasks, and
        superseded nodes that active nodes still depend on. Use it occasionally to tidy up.

        Args:
            stale_days: Age in days after which an open task counts as stale.
        """
        return self.maintenance.review(stale_days).to_markdown()

    def log_session(self, summary: str, details: str | None = None, related: list[str] | None = None) -> str:
        """Record what happened in this working session: a session node in the current project,
        linked ABOUT the project and RELATES_TO the given nodes.

        Args:
            summary: One line describing the session.
            details: Markdown details.
            related: Ids of the decisions, tasks and other nodes the session touched.
        """
        project = self.required_project()
        today = dt.date.today().isoformat()
        base = f"session-{today}-{Schema.slug(project)}"[:80]
        session_id, n = base, 2
        while self.store.get(session_id) is not None:
            session_id, n = f"{base[:76]}-{n}", n + 1
        relations = [Relation("ABOUT", project)] + [Relation("RELATES_TO", r) for r in related or []]
        self.store.upsert(session_id, type="session", title=f"Session {today} · {project}",
                          summary=summary, body=details, relations=relations, project=project)
        return f"Logged `{session_id}`."

    def query_cypher(self, cypher: str, params: dict | None = None) -> str:
        """Run a read-only Cypher query; the database rejects writes. Nodes carry the label
        :Entity plus a type label (:Project, :Decision, ...); project membership is
        (n)-[:IN_PROJECT]->(:Project). Returns up to 100 rows as JSON.

        Args:
            cypher: Cypher query.
            params: Query parameters.
        """
        rows = self.store.read_cypher(cypher, params)
        return json.dumps(rows, indent=1, ensure_ascii=False) if rows else "No rows."

    # ------------------------------------------------------------------ entry point

    def run(self) -> None:
        """Serves MCP over stdio until the client disconnects."""
        self.mcp.run()

    @classmethod
    def main(cls) -> int:
        """Console-script entry point."""
        cls().run()
        return 0
