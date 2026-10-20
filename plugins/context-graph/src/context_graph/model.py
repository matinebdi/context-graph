"""Graph data model, validation and naming rules shared by the store, server and CLI."""

from __future__ import annotations

import datetime as dt
import os
import re
from dataclasses import dataclass, field

import yaml

# Documented default ontology. Any lowercase snake_case type is accepted so users can extend it.
DEFAULT_TYPES = {
    "person": "the user and people they work with",
    "project": "a codebase or long-running effort (carries a filesystem `path`)",
    "decision": "a choice that was made, and why",
    "preference": "how the user likes things done",
    "concept": "domain knowledge, architecture, glossary terms",
    "resource": "URLs, docs, dashboards, tickets",
    "tool": "software, services, libraries in use",
    "task": "open work that spans sessions",
    "session": "a log of one working session",
}

# Types that describe the user or the wider world rather than one project: global by default.
GLOBAL_TYPES = {"person", "preference", "tool", "project"}

SUGGESTED_RELATIONS = [
    "WORKS_ON", "OWNS", "PREFERS", "DECIDED", "PART_OF", "DEPENDS_ON", "USES", "RELATES_TO",
    "SUPERSEDES", "ABOUT", "DOCUMENTED_IN", "BLOCKS", "FOLLOWS_UP",
]
# Managed by the store for project scoping; not settable through link().
SYSTEM_RELATIONS = {"IN_PROJECT"}

ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")
TYPE_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
REL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,39}$")
STATUSES = {"active", "done", "superseded", "archived"}


class GraphError(ValueError):
    pass


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:80].rstrip("-") or "node"


def normalize_path(p: str) -> str:
    """Normalize Windows, Git Bash (/c/Users/...) and POSIX paths for prefix comparison."""
    p = p.strip().replace("\\", "/")
    m = re.match(r"^/([a-zA-Z])/(.*)$", p)
    if m:
        p = f"{m.group(1)}:/{m.group(2)}"
    p = os.path.normcase(os.path.normpath(p)).replace("\\", "/")
    return p.rstrip("/")


def check_id(node_id: str) -> str:
    if not isinstance(node_id, str) or not ID_RE.match(node_id):
        raise GraphError(f"invalid id {node_id!r}: use lowercase letters, digits and dashes (max 80)")
    return node_id


def check_type(node_type: str) -> str:
    t = (node_type or "").strip().lower()
    if not TYPE_RE.match(t):
        raise GraphError(
            f"invalid type {node_type!r}: use snake_case; defaults are {sorted(DEFAULT_TYPES)}"
        )
    return t


def check_status(status: str) -> str:
    if status not in STATUSES:
        raise GraphError(f"invalid status {status!r}; expected one of {sorted(STATUSES)}")
    return status


def to_label(node_type: str) -> str:
    """'decision' -> 'Decision', 'user_story' -> 'UserStory'. Safe to interpolate into Cypher."""
    return "".join(part.capitalize() for part in check_type(node_type).split("_"))


def to_rel(rel: str) -> str:
    """'works_on' / 'WORKS_ON' -> 'WORKS_ON'. Safe to interpolate into Cypher."""
    if not isinstance(rel, str) or not REL_RE.match(rel):
        raise GraphError(f"invalid relation {rel!r}: use snake_case, e.g. {SUGGESTED_RELATIONS[:5]}")
    r = rel.upper()
    if r in SYSTEM_RELATIONS:
        raise GraphError(f"{r} is managed automatically; use the `scope`/`project` arguments instead")
    return r


@dataclass
class Relation:
    type: str
    target: str
    note: str | None = None

    def to_dict(self) -> dict:
        d = {"type": self.type, "target": self.target}
        if self.note:
            d["note"] = self.note
        return d


@dataclass
class Node:
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
        return {
            "id": self.id, "type": self.type, "title": self.title, "summary": self.summary,
            "body": self.body, "status": self.status, "path": self.path, "tags": self.tags,
            "relations": [r.to_dict() for r in self.relations], "projects": self.projects,
            "created": self.created, "updated": self.updated,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Node":
        return cls(
            id=str(d["id"]),
            type=str(d.get("type") or "concept"),
            title=str(d.get("title") or d["id"]),
            summary=str(d.get("summary") or ""),
            body=str(d.get("body") or ""),
            status=str(d.get("status") or "active"),
            path=d.get("path") or None,
            tags=[str(t) for t in d.get("tags") or []],
            relations=[
                Relation(str(r["type"]), str(r["target"]), r.get("note"))
                for r in d.get("relations") or []
                if isinstance(r, dict) and "type" in r and "target" in r
            ],
            projects=[str(p) for p in d.get("projects") or []],
            created=str(d.get("created") or ""),
            updated=str(d.get("updated") or ""),
        )

    @classmethod
    def from_markdown(cls, text: str) -> "Node":
        """Parse the legacy one-file-per-node markdown format (YAML frontmatter + body)."""
        m = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n?(.*)$", text, re.S)
        if not m:
            raise GraphError("missing frontmatter")
        meta = yaml.safe_load(m.group(1)) or {}
        meta["body"] = m.group(2).strip()
        for key in ("created", "updated"):
            if key in meta:
                meta[key] = str(meta[key])
        return cls.from_dict(meta)
