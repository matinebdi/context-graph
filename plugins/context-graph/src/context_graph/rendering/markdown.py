from context_graph.domain import Node
from context_graph.storage import Neo4jStore


class MarkdownRenderer:
    """Markdown views of the graph, shared by the MCP server and the session-start hooks.

    Attributes:
        store: Store the views are read from.
    """

    def __init__(self, store: Neo4jStore):
        self.store = store

    @staticmethod
    def brief(node: dict | Node) -> str:
        """Renders a node as one list item: id, type, status if not active, title and summary.

        Args:
            node: A ``Node`` or a brief dict as returned by ``Neo4jStore.find``.

        Returns:
            A markdown list item.
        """
        data = node.to_dict() if isinstance(node, Node) else node
        status = f" [{data['status']}]" if data.get("status") and data["status"] != "active" else ""
        summary = f" — {data['summary']}" if data.get("summary") else ""
        return f"- **{data['id']}** ({data['type']}){status}: {data['title']}{summary}"

    def node(self, node_id: str, depth: int = 1) -> str:
        """Renders a full node page with its relations, members (for projects) and neighbors.

        Args:
            node_id: Node to render.
            depth: 2 adds the second-degree neighbors.

        Returns:
            The page, or a not-found hint.
        """
        node = self.store.get(node_id)
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

        outgoing, incoming = self.store.neighbors(node_id)
        if outgoing:
            out.append("\n## Outgoing")
            for r in outgoing:
                note = f" — {r['note']}" if r["note"] else ""
                other = r["other"]
                out.append(f"- {r['rel']} → **{other['id']}**: {other['title']} ({other['type']}){note}")
        if incoming:
            out.append("\n## Incoming")
            for r in incoming:
                note = f" — {r['note']}" if r["note"] else ""
                other = r["other"]
                out.append(f"- **{other['id']}**: {other['title']} ({other['type']}) —{r['rel']}→ this{note}")

        if node.type == "project":
            members = self.store.find(project=node.id, include_global=False, status=None,
                                      order="updated", limit=40)
            if members:
                out.append("\n## In this project (most recent first)")
                out.extend(self.brief(m) for m in members)

        if depth >= 2:
            second = self._second_degree(node.id, outgoing + incoming)
            if second:
                out.append("\n## Second-degree neighbors")
                out.extend(second[:40])
        return "\n".join(out)

    def overview(self, project: str | None = None, recent: int = 8) -> str:
        """Renders the graph overview injected at session start.

        Args:
            project: Current project; adds its open tasks, decisions and recent sessions.
            recent: Number of recently updated nodes to list.

        Returns:
            The overview.
        """
        counts = self.store.counts()
        if not counts:
            return "The context graph is empty."
        total = sum(counts.values())
        out = [f"# Context graph: {total} nodes (" + ", ".join(f"{t} {c}" for t, c in counts.items()) + ")"]
        find = self.store.find
        sections = [
            ("People", find(type="person", project=project, limit=26), 25),
            ("Preferences (follow these)", find(type="preference", project=project, limit=26), 25),
            ("Projects", find(type="project", order="updated", limit=11), 10),
        ]
        if project:
            sections += [
                (f"Open tasks in {project}",
                 find(type="task", project=project, include_global=False, order="updated", limit=16), 15),
                (f"Decisions in {project}",
                 find(type="decision", project=project, include_global=False, order="updated", limit=16), 15),
                (f"Recent sessions in {project}",
                 find(type="session", project=project, include_global=False, status=None,
                      order="updated", limit=3), 3),
            ]
        else:
            sections.append(("Open tasks", find(type="task", order="updated", limit=16), 15))
        sections.append(("Recently updated", find(status=None, order="updated", limit=recent), recent))
        for heading, items, limit in sections:
            out.extend(self._section(heading, items, limit))
        return "\n".join(out)

    def project_context(self, directory: str) -> str:
        """Renders the project node(s) containing ``directory``; empty if there is none."""
        projects = self.store.projects_for_path(directory)
        return "\n\n".join(self.node(p.id) for p in projects[:2])

    def _section(self, heading: str, items: list[dict], limit: int) -> list[str]:
        if not items:
            return []
        lines = [f"\n## {heading}", *(self.brief(n) for n in items[:limit])]
        if len(items) > limit:
            lines.append("- …and more (search_graph to find them)")
        return lines

    def _second_degree(self, node_id: str, relations: list[dict]) -> list[str]:
        seen = {node_id}
        lines = []
        for relation in relations:
            via = relation["other"]["id"]
            if via in seen:
                continue
            seen.add(via)
            outgoing, incoming = self.store.neighbors(via)
            for further in outgoing + incoming:
                if further["other"]["id"] not in seen:
                    seen.add(further["other"]["id"])
                    lines.append(f"- via {via}: " + self.brief(further["other"])[2:])
        return lines
