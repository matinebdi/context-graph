import re
from dataclasses import dataclass, field

import yaml

from context_graph.domain.errors import GraphError


@dataclass
class Relation:
    """A directed, typed edge from the node that owns it to ``target``.

    Attributes:
        type: Relationship type, e.g. ``DEPENDS_ON``.
        target: Id of the target node.
        note: Optional short qualifier.
    """

    type: str
    target: str
    note: str | None = None

    def to_dict(self) -> dict:
        """Returns the relation as a plain dict, omitting an empty note."""
        data = {"type": self.type, "target": self.target}
        if self.note:
            data["note"] = self.note
        return data


@dataclass
class Node:
    """A node of the context graph with its outgoing relations and project scopes.

    Attributes:
        id: Unique lowercase-dash slug.
        type: Snake_case node type, e.g. ``decision``.
        title: Human-readable name.
        summary: One-line description.
        body: Markdown details.
        status: One of ``active``, ``done``, ``superseded``, ``archived``.
        path: Filesystem root, for project nodes.
        tags: Lowercase tags.
        relations: Outgoing relations, excluding project membership.
        projects: Ids of the projects the node belongs to; empty means global.
        created: UTC creation timestamp.
        updated: UTC last-update timestamp.
    """

    id: str
    type: str
    title: str
    summary: str = ""
    body: str = ""
    status: str = "active"
    path: str | None = None
    tags: list[str] = field(default_factory=list)
    relations: list[Relation] = field(default_factory=list)
    projects: list[str] = field(default_factory=list)
    created: str = ""
    updated: str = ""

    def to_dict(self) -> dict:
        """Returns the node as a JSON-serializable dict (the export format)."""
        return {
            "id": self.id, "type": self.type, "title": self.title, "summary": self.summary,
            "body": self.body, "status": self.status, "path": self.path, "tags": self.tags,
            "relations": [r.to_dict() for r in self.relations], "projects": self.projects,
            "created": self.created, "updated": self.updated,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Node":
        """Builds a node from a dict, tolerating missing optional fields.

        Args:
            data: Dict with at least an ``id``.

        Returns:
            The node.
        """
        return cls(
            id=str(data["id"]),
            type=str(data.get("type") or "concept"),
            title=str(data.get("title") or data["id"]),
            summary=str(data.get("summary") or ""),
            body=str(data.get("body") or ""),
            status=str(data.get("status") or "active"),
            path=data.get("path") or None,
            tags=[str(t) for t in data.get("tags") or []],
            relations=[
                Relation(str(r["type"]), str(r["target"]), r.get("note"))
                for r in data.get("relations") or []
                if isinstance(r, dict) and "type" in r and "target" in r
            ],
            projects=[str(p) for p in data.get("projects") or []],
            created=str(data.get("created") or ""),
            updated=str(data.get("updated") or ""),
        )

    @classmethod
    def from_markdown(cls, text: str) -> "Node":
        """Parses the legacy one-file-per-node format: YAML frontmatter followed by a markdown body.

        Args:
            text: File contents.

        Returns:
            The node.

        Raises:
            GraphError: If the frontmatter is missing.
        """
        match = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n?(.*)$", text, re.S)
        if not match:
            raise GraphError("missing frontmatter")
        meta = yaml.safe_load(match.group(1)) or {}
        meta["body"] = match.group(2).strip()
        for key in ("created", "updated"):
            if key in meta:
                meta[key] = str(meta[key])
        return cls.from_dict(meta)
