"""Neo4j-backed graph store.

Schema: every node is (:Entity:<TypeLabel> {id, type, title, summary, body, status, tags,
tags_text, path, path_key, created, updated}). Relations are real relationship types with an
optional `note`. Project scoping uses (n)-[:IN_PROJECT]->(:Project). Labels and relationship
types are validated by model.to_label / model.to_rel before being interpolated into Cypher.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
from contextlib import contextmanager

from neo4j import READ_ACCESS, GraphDatabase
from neo4j.exceptions import AuthError, Neo4jError, ServiceUnavailable

from .config import Settings
from .model import (
    GraphError, Node, Relation, check_id, check_status, check_type, normalize_path, now, slugify,
    to_label, to_rel,
)

SCHEMA = [
    "CREATE CONSTRAINT entity_id IF NOT EXISTS FOR (n:Entity) REQUIRE n.id IS UNIQUE",
    "CREATE INDEX entity_type IF NOT EXISTS FOR (n:Entity) ON (n.type)",
    "CREATE INDEX entity_updated IF NOT EXISTS FOR (n:Entity) ON (n.updated)",
    "CREATE INDEX project_path IF NOT EXISTS FOR (n:Project) ON (n.path_key)",
    "CREATE FULLTEXT INDEX entity_text IF NOT EXISTS FOR (n:Entity) "
    "ON EACH [n.id, n.title, n.summary, n.body, n.tags_text]",
]

# Returns one full node (properties, outgoing relations, projects) per row bound to `n`.
NODE_RETURN = """
OPTIONAL MATCH (n)-[r]->(m:Entity) WHERE type(r) <> 'IN_PROJECT'
WITH n, collect(CASE WHEN m IS NULL THEN null ELSE {type: type(r), target: m.id, note: r.note} END) AS rels
OPTIONAL MATCH (n)-[:IN_PROJECT]->(p:Project)
RETURN n {.*} AS props, rels, collect(p.id) AS projects
"""

BRIEF = "{.id, .type, .title, .summary, .status, .updated}"


def _node(rec: dict) -> Node:
    return Node.from_dict({**rec["props"], "relations": rec["rels"], "projects": rec["projects"]})


def _lucene(query: str) -> str:
    tokens = re.findall(r"\w+", query.lower())
    return " OR ".join(f"{t} OR {t}*" for t in tokens)


class Neo4jStore:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings.from_env()
        self._driver = None

    # ---------- connection ----------

    @property
    def driver(self):
        if self._driver is None:
            if not self.settings.password:
                # The password file may have been created by `context-graph up` since we started.
                self.settings = Settings.from_env()
            s = self.settings
            if not s.password:
                raise GraphError(
                    "no Neo4j password configured. Run /context-graph:setup to start the local "
                    "database, or set the plugin's neo4j_* options for an external one."
                )
            self._driver = GraphDatabase.driver(
                s.uri, auth=(s.user, s.password),
                connection_timeout=5, connection_acquisition_timeout=10, max_transaction_retry_time=5,
                notifications_min_severity="OFF",  # schema-free graph: "unknown property" hints are noise
            )
        return self._driver

    def close(self) -> None:
        if self._driver is not None:
            self._driver.close()
            self._driver = None

    @contextmanager
    def _errors(self):
        try:
            yield
        except GraphError:
            raise
        except ServiceUnavailable as e:
            raise GraphError(
                f"cannot reach Neo4j at {self.settings.uri}. Is it running? "
                "Run /context-graph:setup (local Docker) or check the plugin's neo4j_uri option."
            ) from e
        except AuthError as e:
            raise GraphError(f"Neo4j rejected the credentials for user {self.settings.user!r}.") from e
        except Neo4jError as e:
            raise GraphError(f"Neo4j error: {e.message or e}") from e

    def _run(self, query: str, **params) -> list[dict]:
        with self._errors():
            records, _, _ = self.driver.execute_query(query, params, database_=self.settings.database)
        return [r.data() for r in records]

    def _write(self, fn):
        with self._errors(), self.driver.session(database=self.settings.database) as s:
            return s.execute_write(fn)

    def ping(self) -> None:
        with self._errors():
            self.driver.verify_connectivity()

    def init_schema(self) -> None:
        for stmt in SCHEMA:
            self._run(stmt)

    # ---------- reads ----------

    def get(self, node_id: str) -> Node | None:
        rows = self._run("MATCH (n:Entity {id: $id})" + NODE_RETURN, id=node_id)
        return _node(rows[0]) if rows else None

    def _get_many(self, ids: list[str]) -> list[Node]:
        if not ids:
            return []
        rows = self._run("MATCH (n:Entity) WHERE n.id IN $ids" + NODE_RETURN, ids=ids)
        by_id = {r["props"]["id"]: _node(r) for r in rows}
        return [by_id[i] for i in ids if i in by_id]

    def find(
        self,
        type: str | None = None,
        status: str | None = "active",
        project: str | None = None,
        include_global: bool = True,
        order: str = "title",
        limit: int = 25,
    ) -> list[dict]:
        """Brief node dicts filtered by type/status and project scope.

        project=None means every node; with a project, nodes IN_PROJECT that project plus
        (if include_global) nodes that belong to no project.
        """
        where = ["($type IS NULL OR n.type = $type)", "($status IS NULL OR n.status = $status)"]
        if project:
            scoped = "EXISTS { (n)-[:IN_PROJECT]->(:Project {id: $project}) }"
            if include_global:
                scoped = f"({scoped} OR NOT EXISTS {{ (n)-[:IN_PROJECT]->() }})"
            where.append(scoped)
        order_by = "n.updated DESC" if order == "updated" else "toLower(n.title)"
        return [row["n"] for row in self._run(
            f"MATCH (n:Entity) WHERE {' AND '.join(where)} "
            f"RETURN n {BRIEF} AS n ORDER BY {order_by} LIMIT $limit",
            type=type, status=status, project=project, limit=limit,
        )]

    def counts(self) -> dict[str, int]:
        rows = self._run("MATCH (n:Entity) RETURN n.type AS type, count(*) AS c ORDER BY type")
        return {r["type"]: r["c"] for r in rows}

    def neighbors(self, node_id: str) -> tuple[list[dict], list[dict]]:
        """(outgoing, incoming) relations with brief info on the node at the other end."""
        out = self._run(
            f"MATCH (n:Entity {{id: $id}})-[r]->(m:Entity) WHERE type(r) <> 'IN_PROJECT' "
            f"RETURN type(r) AS rel, r.note AS note, m {BRIEF} AS other ORDER BY rel, other.id",
            id=node_id,
        )
        inc = self._run(
            f"MATCH (m:Entity)-[r]->(n:Entity {{id: $id}}) WHERE type(r) <> 'IN_PROJECT' "
            f"RETURN type(r) AS rel, r.note AS note, m {BRIEF} AS other ORDER BY rel, other.id",
            id=node_id,
        )
        return out, inc

    def search(
        self, query: str, type: str | None = None, project: str | None = None, limit: int = 10
    ) -> list[tuple[float, Node]]:
        lucene = _lucene(query)
        if not lucene:
            return []
        rows = self._run(
            """
            CALL db.index.fulltext.queryNodes('entity_text', $q) YIELD node, score
            WHERE $type IS NULL OR node.type = $type
            WITH node, score
              * CASE WHEN $project IS NOT NULL
                       AND EXISTS { (node)-[:IN_PROJECT]->(:Project {id: $project}) }
                     THEN 1.5 ELSE 1.0 END
              * CASE node.status WHEN 'active' THEN 1.0 ELSE 0.5 END AS s
            RETURN node.id AS id, s ORDER BY s DESC, id LIMIT $limit
            """,
            q=lucene, type=type, project=project, limit=limit,
        )
        scores = {r["id"]: r["s"] for r in rows}
        return [(scores[n.id], n) for n in self._get_many([r["id"] for r in rows])]

    def projects_for_path(self, cwd: str) -> list[Node]:
        """Project nodes whose `path` contains cwd, most specific first."""
        here = normalize_path(cwd)
        rows = self._run("MATCH (p:Project) WHERE p.path_key IS NOT NULL RETURN p.id AS id, p.path_key AS k")
        hits = sorted(
            ((len(r["k"]), r["id"]) for r in rows if here == r["k"] or here.startswith(r["k"] + "/")),
            reverse=True,
        )
        return self._get_many([i for _, i in hits])

    def read_cypher(self, query: str, params: dict | None = None, limit: int = 100) -> list[dict]:
        """Run arbitrary Cypher in a READ transaction: the server rejects any write."""
        with self._errors(), self.driver.session(
            database=self.settings.database, default_access_mode=READ_ACCESS
        ) as s:
            rows = s.execute_read(lambda tx: tx.run(query, params or {}).fetch(limit))
        return [json.loads(json.dumps(r.data(), default=str)) for r in rows]

    # ---------- writes ----------

    @staticmethod
    def _save(tx, node: Node, old_type: str | None) -> None:
        label = to_label(node.type)
        remove = ""
        if old_type and old_type != node.type:
            remove = f"REMOVE n:{to_label(old_type)} "
        props = {
            "type": node.type, "title": node.title, "summary": node.summary, "body": node.body,
            "status": node.status, "tags": node.tags, "tags_text": " ".join(node.tags),
            "path": node.path, "path_key": normalize_path(node.path) if node.path else None,
            "created": node.created, "updated": node.updated,
        }
        tx.run(f"MERGE (n:Entity {{id: $id}}) SET n += $props {remove}SET n:{label}",
               id=node.id, props=props)

    @staticmethod
    def _link(tx, source: str, rel: str, target: str, note: str | None, touch: bool = True) -> bool | None:
        """Returns True if created, False if it already existed, None if an endpoint is missing.
        touch=False keeps the source node's `updated` timestamp (used by imports)."""
        rec = tx.run(
            f"""
            MATCH (a:Entity {{id: $a}}) MATCH (b:Entity {{id: $b}})
            OPTIONAL MATCH (a)-[old:{rel}]->(b)
            WITH a, b, count(old) = 0 AS isnew
            MERGE (a)-[r:{rel}]->(b)
            SET r.note = coalesce($note, r.note), a.updated = CASE WHEN $touch THEN $now ELSE a.updated END
            RETURN isnew
            """,
            a=source, b=target, note=note, now=now(), touch=touch,
        ).single()
        return None if rec is None else rec["isnew"]

    @staticmethod
    def _scope(tx, node_id: str, project_id: str) -> None:
        tx.run(
            "MATCH (n:Entity {id: $n}), (p:Project {id: $p}) WHERE n <> p MERGE (n)-[:IN_PROJECT]->(p)",
            n=node_id, p=project_id,
        )

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
        project: str | None = None,
    ) -> tuple[Node, bool]:
        """Create a node or merge the given fields into it. Returns (node, created).

        relations are added as outgoing edges (targets must exist); project adds IN_PROJECT.
        """
        check_id(node_id)
        existing = self.get(node_id)
        created = existing is None
        if created:
            if not type:
                raise GraphError(f"node {node_id!r} does not exist; `type` is required to create it")
            node = Node(id=node_id, type=check_type(type), title=title or node_id, created=now())
            old_type = None
        else:
            node, old_type = existing, existing.type
            if type:
                node.type = check_type(type)
            if title:
                node.title = title
        if summary is not None:
            node.summary = summary.strip()
        if body is not None:
            node.body = body.strip()
        if append_body and append_body.strip():
            stamp = dt.date.today().isoformat()
            node.body = (node.body + f"\n\n**{stamp}:** {append_body.strip()}").strip()
        if tags is not None:
            node.tags = sorted({t.strip().lower() for t in tags if t.strip()})
        if path is not None:
            node.path = path or None
        if status:
            node.status = check_status(status)
        node.updated = now()

        rels = [(to_rel(r.type), check_id(r.target), r.note) for r in relations or []]
        if project:
            check_id(project)

        def work(tx):
            self._save(tx, node, old_type)
            missing = [t for rel, t, note in rels if self._link(tx, node_id, rel, t, note) is None]
            if missing:
                raise GraphError(f"relation targets do not exist: {missing}. Create them first.")
            if project:
                self._scope(tx, node_id, project)

        self._write(work)
        return self.get(node_id), created

    def link(self, source: str, rel: str, target: str, note: str | None = None) -> bool:
        rel = to_rel(rel)
        result = self._write(lambda tx: self._link(tx, source, rel, target, note))
        if result is None:
            missing = [i for i in (source, target) if self.get(i) is None]
            raise GraphError(f"node(s) do not exist: {missing}")
        return result

    def unlink(self, source: str, rel: str, target: str) -> bool:
        rows = self._run(
            f"MATCH (:Entity {{id: $a}})-[r:{to_rel(rel)}]->(:Entity {{id: $b}}) DELETE r RETURN count(*) AS c",
            a=source, b=target,
        )
        return bool(rows and rows[0]["c"])

    def set_project(self, node_id: str, project_id: str, remove: bool = False) -> None:
        if remove:
            self._run("MATCH (:Entity {id: $n})-[r:IN_PROJECT]->(:Project {id: $p}) DELETE r",
                      n=node_id, p=project_id)
        else:
            self._write(lambda tx: self._scope(tx, node_id, project_id))

    def delete(self, node_id: str) -> int:
        """Delete a node and all its relations. Returns how many incoming relations were removed."""
        rows = self._run(
            "MATCH (n:Entity {id: $id}) OPTIONAL MATCH (n)<-[r]-() "
            "WITH n, count(r) AS inc DETACH DELETE n RETURN inc",
            id=node_id,
        )
        if not rows:
            raise GraphError(f"node {node_id!r} does not exist")
        return rows[0]["inc"]

    def ensure_project(self, directory: str) -> Node:
        """Return the project node registered for this directory, creating one if needed."""
        hits = self.projects_for_path(directory)
        if hits:
            return hits[0]
        name = os.path.basename(normalize_path(directory)) or "project"
        base = slugify(name)
        pid, n = base, 2
        while self.get(pid) is not None:
            pid, n = f"{base}-{n}", n + 1
        node, _ = self.upsert(pid, type="project", title=name, path=directory)
        return node

    # ---------- backup ----------

    def export_all(self) -> list[dict]:
        rows = self._run("MATCH (n:Entity)" + NODE_RETURN + " ORDER BY props.id")
        return [_node(r).to_dict() for r in rows]

    def import_nodes(self, nodes: list[Node]) -> int:
        """Load nodes (with relations and project scopes) in one transaction, keeping timestamps."""
        for n in nodes:
            check_id(n.id)
            n.type = check_type(n.type)
            n.created = n.created or now()
            n.updated = n.updated or n.created

        def work(tx):
            for n in nodes:
                self._save(tx, n, None)
            for n in nodes:
                for r in n.relations:
                    if self._link(tx, n.id, to_rel(r.type), r.target, r.note, touch=False) is None:
                        raise GraphError(f"{n.id} -> {r.target}: target missing from import")
                for p in n.projects:
                    self._scope(tx, n.id, p)

        self._write(work)
        return len(nodes)
