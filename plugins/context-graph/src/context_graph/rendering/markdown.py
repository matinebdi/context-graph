from context_graph.domain import Node
from context_graph.storage import Neo4jStore


class MarkdownRenderer:
    """Markdown views of the graph, shared by the MCP server and the session-start hooks.

    Attributes:
        store: Store the views are read from.
    """

    # Maximum length of the session-start overview, in characters (~1,500 tokens).
    OVERVIEW_BUDGET = 6000
    # Room kept for a section's "…and N more" line.
    MORE_LINE_RESERVE = 60

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
        if node.created_by or node.updated_by:
            meta.append(f"by: {node.created_by or '?'}" + (f" → {node.updated_by}" if node.updated_by != node.created_by else ""))
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

    def overview(self, project: str | None = None, budget: int | None = None) -> str:
        """Renders the graph overview injected at session start.

        Sections are filled in priority order with their most important nodes first (see
        ``Ranking.importance``) until ``budget`` characters are used; each section then says how
        many nodes it left out. A node appears in one section at most.

        Args:
            project: Current project; adds its open tasks, decisions and recent sessions.
            budget: Maximum length in characters; defaults to ``OVERVIEW_BUDGET``.

        Returns:
            The overview.
        """
        counts = self.store.counts()
        if not counts:
            return "The context graph is empty."
        budget = budget or self.OVERVIEW_BUDGET
        total = sum(counts.values())
        out = [f"# Context graph: {total} nodes (" + ", ".join(f"{t} {c}" for t, c in counts.items()) + ")"]
        used, shown = len(out[0]), set()
        for heading, query, limit in self._overview_sections(project):
            items = [n for n in self.store.find(**query, limit=limit) if n["id"] not in shown]
            if not items:
                continue
            lines = [f"\n## {heading}"]
            for item in items:
                line = self.brief(item)
                if used + sum(len(x) + 1 for x in lines) + len(line) + 1 > budget - self.MORE_LINE_RESERVE:
                    break
                lines.append(line)
                shown.add(item["id"])
            if len(lines) == 1:
                continue
            listed = len(lines) - 1
            if heading != "Recently updated":
                filters = {k: v for k, v in query.items() if k != "order"}
                hidden = self.store.count(**filters) - listed
                if hidden > 0:
                    lines.append(f"- …and {hidden} more (search_graph to find them)")
            out.extend(lines)
            used += sum(len(x) + 1 for x in lines)
        return "\n".join(out)

    @staticmethod
    def _overview_sections(project: str | None) -> list[tuple[str, dict, int]]:
        """Overview sections in priority order: ``(heading, find() filters, max items)``."""
        sections = [
            ("Preferences (follow these)", {"type": "preference", "project": project, "order": "importance"}, 25),
            ("People", {"type": "person", "project": project, "order": "importance"}, 10),
        ]
        if project:
            scoped = {"project": project, "include_global": False}
            sections += [
                (f"Open tasks in {project}", {"type": "task", **scoped, "order": "importance"}, 15),
                (f"Decisions in {project}", {"type": "decision", **scoped, "order": "importance"}, 15),
                (f"Recent sessions in {project}", {"type": "session", **scoped, "status": None, "order": "updated"}, 3),
            ]
        else:
            sections.append(("Open tasks", {"type": "task", "order": "importance"}, 15))
        sections += [
            ("Projects", {"type": "project", "order": "importance"}, 10),
            ("Recently updated", {"status": None, "order": "updated"}, 8),
        ]
        return sections

    def project_context(self, directory: str) -> str:
        """Renders the project node(s) containing ``directory``; empty if there is none."""
        projects = self.store.projects_for_path(directory)
        return "\n\n".join(self.node(p.id) for p in projects[:2])

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
