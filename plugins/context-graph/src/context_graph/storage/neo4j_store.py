import datetime as dt
import json
import os
import re

from neo4j import READ_ACCESS, GraphDatabase
from neo4j.exceptions import AuthError, Neo4jError, ServiceUnavailable

from context_graph.config import Settings
from context_graph.domain import Clock, GraphError, Node, PathKey, Relation, Schema


class Neo4jErrorTranslator:
    """Context manager that turns driver exceptions into ``GraphError`` with actionable messages.

    Attributes:
        settings: Connection settings, quoted in the messages.
    """

    def __init__(self, settings: Settings):
        self.settings = settings

    def __enter__(self) -> "Neo4jErrorTranslator":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc is None or isinstance(exc, GraphError):
            return False
        if isinstance(exc, ServiceUnavailable):
            raise GraphError(
                f"cannot reach Neo4j at {self.settings.uri}. Is it running? Start it with "
                "`context-graph up` (/context-graph:setup in Claude Code), or check the configured URI."
            ) from exc
        if isinstance(exc, AuthError):
            raise GraphError(f"Neo4j rejected the credentials for user {self.settings.user!r}.") from exc
        if isinstance(exc, Neo4jError):
            raise GraphError(f"Neo4j error: {exc.message or exc}") from exc
        return False


class Neo4jStore:
    """Graph store backed by Neo4j.

    Every node is ``(:Entity:<TypeLabel> {id, type, title, summary, body, status, tags, tags_text,
    path, path_key, created, updated})``. Relations are real relationship types with an optional
    ``note``. Project scoping uses ``(n)-[:IN_PROJECT]->(:Project)``.

    Attributes:
        settings: Connection settings; re-read on the next connection attempt while no password
            is configured, so a database started mid-session is picked up.
    """

    SCHEMA = (
        "CREATE CONSTRAINT entity_id IF NOT EXISTS FOR (n:Entity) REQUIRE n.id IS UNIQUE",
        "CREATE INDEX entity_type IF NOT EXISTS FOR (n:Entity) ON (n.type)",
        "CREATE INDEX entity_updated IF NOT EXISTS FOR (n:Entity) ON (n.updated)",
        "CREATE INDEX project_path IF NOT EXISTS FOR (n:Project) ON (n.path_key)",
        "CREATE FULLTEXT INDEX entity_text IF NOT EXISTS FOR (n:Entity) "
        "ON EACH [n.id, n.title, n.summary, n.body, n.tags_text]",
    )
    # Appended to a MATCH binding `n`: one row per node with its properties, relations and projects.
    NODE_RETURN = """
    OPTIONAL MATCH (n)-[r]->(m:Entity) WHERE type(r) <> 'IN_PROJECT'
    WITH n, collect(CASE WHEN m IS NULL THEN null ELSE {type: type(r), target: m.id, note: r.note} END) AS rels
    OPTIONAL MATCH (n)-[:IN_PROJECT]->(p:Project)
    RETURN n {.*} AS props, rels, collect(p.id) AS projects
    """
    BRIEF = "{.id, .type, .title, .summary, .status, .updated}"

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings.from_environment()
        self._driver = None

    # ------------------------------------------------------------------ connection

    @property
    def driver(self):
        """The lazily created Neo4j driver.

        Raises:
            GraphError: If no password is configured.
        """
        if self._driver is None:
            if not self.settings.password:
                self.settings = Settings.from_environment()
            if not self.settings.password:
                raise GraphError(
                    "no Neo4j password configured. Start the local database with `context-graph up` "
                    "(/context-graph:setup in Claude Code), or configure an external one with the "
                    "CONTEXT_GRAPH_NEO4J_* settings."
                )
            self._driver = GraphDatabase.driver(
                self.settings.uri,
                auth=(self.settings.user, self.settings.password),
                connection_timeout=5,
                connection_acquisition_timeout=10,
                max_transaction_retry_time=5,
                notifications_min_severity="OFF",  # schema-free graph: "unknown property" hints are noise
            )
        return self._driver

    def close(self) -> None:
        """Closes the driver, if it was opened."""
        if self._driver is not None:
            self._driver.close()
            self._driver = None

    def ping(self) -> None:
        """Checks that the database is reachable and accepts the credentials.

        Raises:
            GraphError: If it is not.
        """
        with Neo4jErrorTranslator(self.settings):
            self.driver.verify_connectivity()

    def init_schema(self) -> None:
        """Creates the constraints and indexes; safe to run repeatedly."""
        for statement in self.SCHEMA:
            self._run(statement)

    def _run(self, query: str, **params) -> list[dict]:
        with Neo4jErrorTranslator(self.settings):
            records, _, _ = self.driver.execute_query(query, params, database_=self.settings.database)
        return [record.data() for record in records]

    def _write(self, work, *args):
        with Neo4jErrorTranslator(self.settings), self.driver.session(database=self.settings.database) as s:
            return s.execute_write(work, *args)

    # ------------------------------------------------------------------ reads

    def get(self, node_id: str) -> Node | None:
        """Returns the node with this id, or None."""
        rows = self._run("MATCH (n:Entity {id: $id})" + self.NODE_RETURN, id=node_id)
        return self._to_node(rows[0]) if rows else None

    def get_many(self, ids: list[str]) -> list[Node]:
        """Returns the existing nodes among ``ids``, in the order given."""
        if not ids:
            return []
        rows = self._run("MATCH (n:Entity) WHERE n.id IN $ids" + self.NODE_RETURN, ids=ids)
        by_id = {row["props"]["id"]: self._to_node(row) for row in rows}
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
        """Lists brief node dicts.

        Args:
            type: Only nodes of this type.
            status: Only nodes with this status; None for any.
            project: Only nodes in this project; None for the whole graph.
            include_global: With a project, also include nodes that belong to no project.
            order: ``title`` (alphabetical) or ``updated`` (most recent first).
            limit: Maximum number of nodes.

        Returns:
            Dicts with ``id, type, title, summary, status, updated``.
        """
        where = ["($type IS NULL OR n.type = $type)", "($status IS NULL OR n.status = $status)"]
        if project:
            scoped = "EXISTS { (n)-[:IN_PROJECT]->(:Project {id: $project}) }"
            if include_global:
                scoped = f"({scoped} OR NOT EXISTS {{ (n)-[:IN_PROJECT]->() }})"
            where.append(scoped)
        order_by = "n.updated DESC" if order == "updated" else "toLower(n.title)"
        rows = self._run(
            f"MATCH (n:Entity) WHERE {' AND '.join(where)} "
            f"RETURN n {self.BRIEF} AS n ORDER BY {order_by} LIMIT $limit",
            type=type, status=status, project=project, limit=limit,
        )
        return [row["n"] for row in rows]

    def counts(self) -> dict[str, int]:
        """Returns the number of nodes per type."""
        rows = self._run("MATCH (n:Entity) RETURN n.type AS type, count(*) AS c ORDER BY type")
        return {row["type"]: row["c"] for row in rows}

    def neighbors(self, node_id: str) -> tuple[list[dict], list[dict]]:
        """Returns the node's outgoing and incoming relations.

        Args:
            node_id: Node to inspect.

        Returns:
            ``(outgoing, incoming)``, each a list of ``{rel, note, other}`` where ``other`` is a
            brief dict of the node at the other end.
        """
        tail = f"WHERE type(r) <> 'IN_PROJECT' RETURN type(r) AS rel, r.note AS note, m {self.BRIEF} AS other ORDER BY rel, other.id"
        outgoing = self._run(f"MATCH (n:Entity {{id: $id}})-[r]->(m:Entity) {tail}", id=node_id)
        incoming = self._run(f"MATCH (m:Entity)-[r]->(n:Entity {{id: $id}}) {tail}", id=node_id)
        return outgoing, incoming

    def search(
        self, query: str, type: str | None = None, project: str | None = None, limit: int = 10
    ) -> list[tuple[float, Node]]:
        """Full-text search, boosting the given project and demoting inactive nodes.

        Args:
            query: Free text; Lucene operators are ignored.
            type: Only nodes of this type.
            project: Project whose nodes rank 1.5x higher.
            limit: Maximum number of results.

        Returns:
            ``(score, node)`` pairs, best first.
        """
        lucene = self.lucene_query(query)
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
        scores = {row["id"]: row["s"] for row in rows}
        return [(scores[n.id], n) for n in self.get_many([row["id"] for row in rows])]

    def projects_for_path(self, directory: str) -> list[Node]:
        """Returns the project nodes whose ``path`` contains ``directory``, most specific first."""
        here = PathKey.normalize(directory)
        rows = self._run("MATCH (p:Project) WHERE p.path_key IS NOT NULL RETURN p.id AS id, p.path_key AS k")
        hits = sorted(
            ((len(row["k"]), row["id"]) for row in rows
             if here == row["k"] or here.startswith(row["k"] + "/")),
            reverse=True,
        )
        return self.get_many([node_id for _, node_id in hits])

    def read_cypher(self, query: str, params: dict | None = None, limit: int = 100) -> list[dict]:
        """Runs arbitrary Cypher in a read transaction, in which the server rejects any write.

        Args:
            query: Cypher query.
            params: Query parameters.
            limit: Maximum number of rows returned.

        Returns:
            Rows as JSON-compatible dicts.

        Raises:
            GraphError: If the query fails or attempts a write.
        """
        with Neo4jErrorTranslator(self.settings), self.driver.session(
            database=self.settings.database, default_access_mode=READ_ACCESS
        ) as session:
            records = session.execute_read(self._fetch, query, params or {}, limit)
        return [json.loads(json.dumps(record.data(), default=str)) for record in records]

    # ------------------------------------------------------------------ writes

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
        """Creates a node or merges the given fields into it, atomically.

        Args:
            node_id: Id of the node.
            type: Node type; required when creating.
            title: New title.
            summary: New one-line summary.
            body: New markdown body (replaces the old one).
            append_body: Paragraph appended to the body with today's date.
            tags: New tags (replace the old ones).
            relations: Outgoing relations to add; their targets must exist.
            path: Filesystem root, for project nodes.
            status: New status.
            project: Project to attach the node to.

        Returns:
            ``(node, created)``.

        Raises:
            GraphError: On invalid input or a missing relation target; nothing is written then.
        """
        Schema.node_id(node_id)
        existing = self.get(node_id)
        created = existing is None
        if created:
            if not type:
                raise GraphError(f"node {node_id!r} does not exist; `type` is required to create it")
            node = Node(id=node_id, type=Schema.node_type(type), title=title or node_id, created=Clock.now())
            old_type = None
        else:
            node, old_type = existing, existing.type
            if type:
                node.type = Schema.node_type(type)
            if title:
                node.title = title
        if summary is not None:
            node.summary = summary.strip()
        if body is not None:
            node.body = body.strip()
        if append_body and append_body.strip():
            node.body = (node.body + f"\n\n**{dt.date.today().isoformat()}:** {append_body.strip()}").strip()
        if tags is not None:
            node.tags = sorted({t.strip().lower() for t in tags if t.strip()})
        if path is not None:
            node.path = path or None
        if status:
            node.status = Schema.status(status)
        node.updated = Clock.now()

        links = [(Schema.relationship(r.type), Schema.node_id(r.target), r.note) for r in relations or []]
        if project:
            Schema.node_id(project)
        self._write(self._upsert_tx, node, old_type, links, project)
        return self.get(node_id), created

    def link(self, source: str, relation: str, target: str, note: str | None = None) -> bool:
        """Adds a relation between two existing nodes.

        Returns:
            True if it was created, False if it already existed.

        Raises:
            GraphError: If the relation name is invalid or a node is missing.
        """
        result = self._write(self._link_tx, source, Schema.relationship(relation), target, note, True)
        if result is None:
            missing = [i for i in (source, target) if self.get(i) is None]
            raise GraphError(f"node(s) do not exist: {missing}")
        return result

    def unlink(self, source: str, relation: str, target: str) -> bool:
        """Removes a relation. Returns whether one was removed."""
        rows = self._run(
            f"MATCH (:Entity {{id: $a}})-[r:{Schema.relationship(relation)}]->(:Entity {{id: $b}}) "
            "DELETE r RETURN count(*) AS c",
            a=source, b=target,
        )
        return bool(rows and rows[0]["c"])

    def set_project(self, node_id: str, project_id: str, remove: bool = False) -> None:
        """Attaches a node to a project, or detaches it with ``remove=True``."""
        if remove:
            self._run("MATCH (:Entity {id: $n})-[r:IN_PROJECT]->(:Project {id: $p}) DELETE r",
                      n=node_id, p=project_id)
        else:
            self._write(self._scope_tx, node_id, project_id)

    def delete(self, node_id: str) -> int:
        """Deletes a node and all its relations.

        Returns:
            How many incoming relations were removed.

        Raises:
            GraphError: If the node does not exist.
        """
        rows = self._run(
            "MATCH (n:Entity {id: $id}) OPTIONAL MATCH (n)<-[r]-() "
            "WITH n, count(r) AS inc DETACH DELETE n RETURN inc",
            id=node_id,
        )
        if not rows:
            raise GraphError(f"node {node_id!r} does not exist")
        return rows[0]["inc"]

    def ensure_project(self, directory: str) -> Node:
        """Returns the project node registered for ``directory``, creating one if needed.

        A new project gets the slug of the folder name as id, with ``-2``, ``-3``... on collision.
        """
        hits = self.projects_for_path(directory)
        if hits:
            return hits[0]
        name = os.path.basename(PathKey.normalize(directory)) or "project"
        base = Schema.slug(name)
        project_id, n = base, 2
        while self.get(project_id) is not None:
            project_id, n = f"{base}-{n}", n + 1
        node, _ = self.upsert(project_id, type="project", title=name, path=directory)
        return node

    # ------------------------------------------------------------------ backup

    def export_all(self) -> list[dict]:
        """Returns every node as an export dict, sorted by id."""
        rows = self._run("MATCH (n:Entity)" + self.NODE_RETURN + " ORDER BY props.id")
        return [self._to_node(row).to_dict() for row in rows]

    def import_nodes(self, nodes: list[Node]) -> int:
        """Loads nodes with their relations and projects in one transaction, keeping timestamps.

        Returns:
            The number of nodes loaded.

        Raises:
            GraphError: If a node is invalid or a relation target is missing; nothing is written then.
        """
        for node in nodes:
            Schema.node_id(node.id)
            node.type = Schema.node_type(node.type)
            node.created = node.created or Clock.now()
            node.updated = node.updated or node.created
        self._write(self._import_tx, nodes)
        return len(nodes)

    # ------------------------------------------------------------------ transaction functions

    def _upsert_tx(self, tx, node: Node, old_type: str | None, links: list, project: str | None) -> None:
        self._save_tx(tx, node, old_type)
        missing = [target for rel, target, note in links if self._link_tx(tx, node.id, rel, target, note) is None]
        if missing:
            raise GraphError(f"relation targets do not exist: {missing}. Create them first.")
        if project:
            self._scope_tx(tx, node.id, project)

    def _import_tx(self, tx, nodes: list[Node]) -> None:
        for node in nodes:
            self._save_tx(tx, node, None)
        for node in nodes:
            for relation in node.relations:
                rel = Schema.relationship(relation.type)
                if self._link_tx(tx, node.id, rel, relation.target, relation.note, False) is None:
                    raise GraphError(f"{node.id} -> {relation.target}: target missing from import")
            for project in node.projects:
                self._scope_tx(tx, node.id, project)

    @staticmethod
    def _save_tx(tx, node: Node, old_type: str | None) -> None:
        label = Schema.label(node.type)
        remove = f"REMOVE n:{Schema.label(old_type)} " if old_type and old_type != node.type else ""
        props = {
            "type": node.type, "title": node.title, "summary": node.summary, "body": node.body,
            "status": node.status, "tags": node.tags, "tags_text": " ".join(node.tags),
            "path": node.path, "path_key": PathKey.normalize(node.path) if node.path else None,
            "created": node.created, "updated": node.updated,
        }
        tx.run(f"MERGE (n:Entity {{id: $id}}) SET n += $props {remove}SET n:{label}", id=node.id, props=props)

    @staticmethod
    def _link_tx(tx, source: str, rel: str, target: str, note: str | None, touch: bool = True) -> bool | None:
        """Returns True if created, False if it existed, None if an endpoint is missing.

        ``touch=False`` keeps the source node's ``updated`` timestamp (used by imports).
        """
        record = tx.run(
            f"""
            MATCH (a:Entity {{id: $a}}) MATCH (b:Entity {{id: $b}})
            OPTIONAL MATCH (a)-[old:{rel}]->(b)
            WITH a, b, count(old) = 0 AS isnew
            MERGE (a)-[r:{rel}]->(b)
            SET r.note = coalesce($note, r.note),
                a.updated = CASE WHEN $touch THEN $now ELSE a.updated END
            RETURN isnew
            """,
            a=source, b=target, note=note, now=Clock.now(), touch=touch,
        ).single()
        return None if record is None else record["isnew"]

    @staticmethod
    def _scope_tx(tx, node_id: str, project_id: str) -> None:
        tx.run(
            "MATCH (n:Entity {id: $n}), (p:Project {id: $p}) WHERE n <> p MERGE (n)-[:IN_PROJECT]->(p)",
            n=node_id, p=project_id,
        )

    @staticmethod
    def _fetch(tx, query: str, params: dict, limit: int) -> list:
        return tx.run(query, params).fetch(limit)

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _to_node(row: dict) -> Node:
        return Node.from_dict({**row["props"], "relations": row["rels"], "projects": row["projects"]})

    @staticmethod
    def lucene_query(text: str) -> str:
        """Builds a Lucene query matching each word exactly or as a prefix.

        Tokenizing on word characters drops every Lucene operator and special character.

        Args:
            text: Free text.

        Returns:
            E.g. ``rotate OR rotate* OR jwt OR jwt*``; empty if there are no words.
        """
        tokens = re.findall(r"\w+", text.lower())
        return " OR ".join(f"{t} OR {t}*" for t in tokens)
