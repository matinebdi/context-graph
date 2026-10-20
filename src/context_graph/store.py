from __future__ import annotations

import datetime as dt
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import yaml

NODE_TYPES = {
    "person",      # the user and people they work with
    "project",     # a codebase or long-running effort (may carry a filesystem `path`)
    "decision",    # a choice that was made, and why
    "preference",  # how the user likes things done
    "concept",     # domain knowledge, architecture, glossary terms
    "resource",    # URLs, docs, dashboards, tickets
    "tool",        # software, services, libraries the user relies on
    "task",        # open work items that span sessions
    "session",     # a log of one working session
}

# Suggested vocabulary; any snake_case relation is accepted.
SUGGESTED_RELATIONS = [
    "works_on", "owns", "prefers", "decided", "part_of", "depends_on", "uses",
    "relates_to", "supersedes", "about", "documented_in", "blocks", "follows_up",
]

ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")
REL_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
FIELD_ORDER = ["id", "type", "title", "summary", "status", "path", "tags", "relations", "created", "updated"]


def default_data_dir() -> Path:
    env = os.environ.get("CONTEXT_GRAPH_DIR")
    if env:
        return Path(env).expanduser()
    return Path(__file__).resolve().parents[2] / "graph"


def now() -> str:
    return dt.datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M%z")


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:80] or "node"


def normalize_path(p: str) -> str:
    """Normalize Windows, Git Bash (/c/Users/...) and POSIX paths for prefix comparison."""
    p = p.strip().replace("\\", "/")
    m = re.match(r"^/([a-zA-Z])/(.*)$", p)
    if m:
        p = f"{m.group(1)}:/{m.group(2)}"
    p = os.path.normcase(os.path.normpath(p)).replace("\\", "/")
    return p.rstrip("/")


class GraphError(ValueError):
    pass


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
    status: str = "active"
    path: str | None = None
    tags: list[str] = field(default_factory=list)
    relations: list[Relation] = field(default_factory=list)
    created: str = ""
    updated: str = ""
    body: str = ""

    def to_markdown(self) -> str:
        meta = {
            "id": self.id,
            "type": self.type,
            "title": self.title,
            "summary": self.summary,
            "status": self.status,
            "path": self.path,
            "tags": self.tags,
            "relations": [r.to_dict() for r in self.relations],
            "created": self.created,
            "updated": self.updated,
        }
        meta = {k: meta[k] for k in FIELD_ORDER if meta[k] not in (None, [], "")}
        front = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True, width=100)
        body = self.body.strip()
        return f"---\n{front}---\n" + (f"\n{body}\n" if body else "")

    @classmethod
    def from_markdown(cls, text: str) -> "Node":
        m = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n?(.*)$", text, re.S)
        if not m:
            raise GraphError("missing frontmatter")
        meta = yaml.safe_load(m.group(1)) or {}
        rels = [
            Relation(str(r["type"]), str(r["target"]), r.get("note"))
            for r in meta.get("relations") or []
            if isinstance(r, dict) and "type" in r and "target" in r
        ]
        return cls(
            id=str(meta["id"]),
            type=str(meta.get("type", "concept")),
            title=str(meta.get("title", meta["id"])),
            summary=str(meta.get("summary") or ""),
            status=str(meta.get("status") or "active"),
            path=meta.get("path"),
            tags=[str(t) for t in meta.get("tags") or []],
            relations=rels,
            created=str(meta.get("created") or ""),
            updated=str(meta.get("updated") or ""),
            body=m.group(2).strip(),
        )


class GraphStore:
    def __init__(self, data_dir: Path | str | None = None, autocommit: bool = True):
        self.data_dir = Path(data_dir) if data_dir else default_data_dir()
        self.nodes_dir = self.data_dir / "nodes"
        self.nodes_dir.mkdir(parents=True, exist_ok=True)
        self.autocommit = autocommit

    # ---------- low-level IO ----------

    def _file(self, node_id: str) -> Path:
        return self.nodes_dir / f"{node_id}.md"

    def load_all(self) -> dict[str, Node]:
        nodes: dict[str, Node] = {}
        for f in sorted(self.nodes_dir.glob("*.md")):
            try:
                node = Node.from_markdown(f.read_text(encoding="utf-8"))
            except Exception:
                continue  # a hand-edited broken file must not take the whole graph down
            nodes[node.id] = node
        return nodes

    def get(self, node_id: str) -> Node | None:
        f = self._file(node_id)
        if not f.exists():
            return None
        return Node.from_markdown(f.read_text(encoding="utf-8"))

    def _write(self, node: Node) -> None:
        target = self._file(node.id)
        fd, tmp = tempfile.mkstemp(dir=self.nodes_dir, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(node.to_markdown())
        os.replace(tmp, target)

    def commit(self, message: str) -> bool:
        """Best-effort git commit of the data dir; silently skipped when not in a repo."""
        if not self.autocommit:
            return False
        run = lambda *args: subprocess.run(  # noqa: E731
            ["git", *args], cwd=self.data_dir, capture_output=True, text=True, timeout=20
        )
        try:
            if run("rev-parse", "--is-inside-work-tree").returncode != 0:
                return False
            run("add", "-A", ".")
            return run("commit", "-q", "-m", f"graph: {message}", "--", ".").returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    # ---------- validation ----------

    @staticmethod
    def _check_id(node_id: str) -> None:
        if not ID_RE.match(node_id):
            raise GraphError(
                f"invalid id {node_id!r}: use lowercase letters, digits and dashes (max 80 chars)"
            )

    @staticmethod
    def _check_type(node_type: str) -> None:
        if node_type not in NODE_TYPES:
            raise GraphError(f"unknown node type {node_type!r}; expected one of {sorted(NODE_TYPES)}")

    @staticmethod
    def _check_rel(rel: str) -> None:
        if not REL_RE.match(rel):
            raise GraphError(f"invalid relation {rel!r}: use snake_case, e.g. {SUGGESTED_RELATIONS[:5]}")

    # ---------- mutations ----------

    def upsert(
        self,
        node_id: str,
        type: str | None = None,
        title: str | None = None,
        summary: str | None = None,
        body: str | None = None,
        append_body: str | None = None,
        tags: list[str] | None = None,
        relations: list[Relation] | None = None,
        path: str | None = None,
        status: str | None = None,
        commit: bool = True,
    ) -> tuple[Node, bool]:
        """Create a node or merge fields into an existing one. Returns (node, created)."""
        self._check_id(node_id)
        existing = self.get(node_id)
        created = existing is None
        if created:
            if not type:
                raise GraphError(f"node {node_id!r} does not exist; `type` is required to create it")
            self._check_type(type)
            node = Node(id=node_id, type=type, title=title or node_id, created=now())
        else:
            node = existing
            if type:
                self._check_type(type)
                node.type = type
            if title:
                node.title = title

        if summary is not None:
            node.summary = summary.strip()
        if body is not None:
            node.body = body.strip()
        if append_body:
            stamp = dt.date.today().isoformat()
            node.body = (node.body + f"\n\n**{stamp}:** {append_body.strip()}").strip()
        if tags is not None:
            node.tags = sorted({t.strip().lower() for t in tags if t.strip()})
        if path is not None:
            node.path = path or None
        if status:
            node.status = status
        for rel in relations or []:
            self._check_rel(rel.type)
            self._check_id(rel.target)
            self._add_rel(node, rel)

        node.updated = now()
        self._write(node)
        if commit:
            self.commit(f"{'create' if created else 'update'} {node_id}")
        return node, created

    @staticmethod
    def _add_rel(node: Node, rel: Relation) -> bool:
        for r in node.relations:
            if r.type == rel.type and r.target == rel.target:
                if rel.note:
                    r.note = rel.note
                return False
        node.relations.append(rel)
        return True

    def link(self, source: str, rel: str, target: str, note: str | None = None) -> bool:
        self._check_rel(rel)
        node = self.get(source)
        if node is None:
            raise GraphError(f"source node {source!r} does not exist")
        if self.get(target) is None:
            raise GraphError(f"target node {target!r} does not exist; create it first")
        added = self._add_rel(node, Relation(rel, target, note))
        node.updated = now()
        self._write(node)
        self.commit(f"link {source} -{rel}-> {target}")
        return added

    def unlink(self, source: str, rel: str, target: str) -> bool:
        node = self.get(source)
        if node is None:
            raise GraphError(f"source node {source!r} does not exist")
        before = len(node.relations)
        node.relations = [r for r in node.relations if not (r.type == rel and r.target == target)]
        if len(node.relations) == before:
            return False
        node.updated = now()
        self._write(node)
        self.commit(f"unlink {source} -{rel}-> {target}")
        return True

    def delete(self, node_id: str) -> int:
        """Delete a node and every relation pointing at it. Returns the number of edges removed."""
        f = self._file(node_id)
        if not f.exists():
            raise GraphError(f"node {node_id!r} does not exist")
        f.unlink()
        removed = 0
        for node in self.load_all().values():
            keep = [r for r in node.relations if r.target != node_id]
            if len(keep) != len(node.relations):
                removed += len(node.relations) - len(keep)
                node.relations = keep
                node.updated = now()
                self._write(node)
        self.commit(f"delete {node_id}")
        return removed

    # ---------- queries ----------

    @staticmethod
    def incoming(nodes: dict[str, Node], node_id: str) -> list[tuple[Node, Relation]]:
        return [(n, r) for n in nodes.values() for r in n.relations if r.target == node_id]

    def search(self, query: str, type: str | None = None, limit: int = 10) -> list[tuple[float, Node]]:
        tokens = [t for t in re.split(r"[^\w]+", query.lower()) if t]
        if not tokens:
            return []
        phrase = query.lower().strip()
        results = []
        for node in self.load_all().values():
            if type and node.type != type:
                continue
            fields = [
                (node.id.lower(), 3.0),
                (node.title.lower(), 3.0),
                (" ".join(node.tags), 2.0),
                (node.summary.lower(), 2.0),
                (node.body.lower(), 1.0),
                (" ".join(r.type + " " + r.target for r in node.relations), 0.5),
            ]
            score = sum(w for t in tokens for text, w in fields if t in text)
            if phrase in node.title.lower() or phrase in node.summary.lower():
                score += 3
            if score > 0:
                if node.status != "active":
                    score *= 0.5
                results.append((score, node))
        results.sort(key=lambda x: (-x[0], x[1].id))
        return results[:limit]

    def projects_for_path(self, cwd: str) -> list[Node]:
        """Project nodes whose `path` contains cwd, most specific first."""
        here = normalize_path(cwd)
        hits = []
        for node in self.load_all().values():
            if node.type == "project" and node.path:
                root = normalize_path(node.path)
                if here == root or here.startswith(root + "/"):
                    hits.append((len(root), node))
        return [n for _, n in sorted(hits, key=lambda x: -x[0])]

    def dangling(self) -> list[tuple[str, str, str]]:
        nodes = self.load_all()
        return [(n.id, r.type, r.target) for n in nodes.values() for r in n.relations if r.target not in nodes]
