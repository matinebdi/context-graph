import json
import os
import shutil

from context_graph.domain import GraphError
from context_graph.rendering import MarkdownRenderer
from context_graph.storage import Neo4jStore


class SessionStartHook:
    """Builds the context injected when a client session starts.

    Never fails: when the graph is unreachable it returns a one-line notice that tells the agent
    what the user can do about it.

    Attributes:
        store: Graph store.
        renderer: Markdown views over the store.
        client: ``claude`` (plain-text output) or ``cursor`` (JSON output).
    """

    CLIENTS = ("claude", "cursor")
    FOOTER = (
        "---\n"
        "This is your persistent context graph (MCP server `context-graph`, Neo4j). Use it as long-term\n"
        "memory: search_graph / get_node before asking the user things they may have told you already, and\n"
        "record durable facts, preferences, decisions (with the why), components, resources and open tasks\n"
        "with upsert_node as you learn them. Call log_session after meaningful work."
    )
    START = {"claude": "/context-graph:setup", "cursor": "`context-graph up` in a terminal"}
    EXTERNAL = {
        "claude": "the plugin's neo4j_* options in /config",
        "cursor": "CONTEXT_GRAPH_NEO4J_URI / _USER / _PASSWORD in the env of .cursor/mcp.json",
    }

    def __init__(self, store: Neo4jStore, client: str = "claude"):
        self.store = store
        self.renderer = MarkdownRenderer(store)
        self.client = client

    def respond(self, stdin_text: str) -> str:
        """Returns the hook output for the hook's stdin payload, in the client's format.

        Args:
            stdin_text: JSON the client passes on stdin; may be empty.
        """
        try:
            payload = json.loads(stdin_text or "{}")
        except json.JSONDecodeError:
            payload = {}
        context = self.context(self.directory(payload))
        return json.dumps({"additional_context": context}) if self.client == "cursor" else context

    def context(self, directory: str) -> str:
        """Returns the overview and project page for ``directory``, or an offline notice."""
        try:
            self.store.ping()
            project = self.store.ensure_project(directory)
            return "\n\n".join([
                self.renderer.overview(project.id),
                f"# Current project: `{project.id}` ({directory})\n\n" + self.renderer.node(project.id),
                self.FOOTER,
            ])
        except GraphError as exc:
            reason = str(exc).split(". ")[0]
            return f"[context-graph] The context graph is offline ({reason}). {self.offline_hint()}"

    def offline_hint(self) -> str:
        """Returns what the user can do when the graph is unreachable, for this client."""
        if self.store.settings.is_local and shutil.which("docker") is None:
            return (
                "Docker is not installed (or not on PATH), and the local database needs it. Tell the "
                f"user to either install Docker Desktop and run {self.START[self.client]}, or point the "
                "context graph at an external Neo4j (for example a free Neo4j Aura instance) with "
                f"{self.EXTERNAL[self.client]}."
            )
        return f"Tell the user they can run {self.START[self.client]} to start it."

    @staticmethod
    def directory(payload: dict) -> str:
        """Resolves the project directory of the session.

        Order: ``CONTEXT_GRAPH_PROJECT_DIR``, ``CLAUDE_PROJECT_DIR``, the payload's first
        ``workspace_roots`` entry (Cursor), its ``cwd`` (Claude Code), the working directory.

        Args:
            payload: Hook stdin JSON.
        """
        roots = payload.get("workspace_roots") or []
        return (
            os.environ.get("CONTEXT_GRAPH_PROJECT_DIR")
            or os.environ.get("CLAUDE_PROJECT_DIR")
            or (roots[0] if roots else None)
            or payload.get("cwd")
            or os.getcwd()
        )
