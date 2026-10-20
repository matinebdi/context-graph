import datetime as dt
import json
import os
import re

from neo4j import READ_ACCESS, GraphDatabase
from neo4j.exceptions import AuthError, Neo4jError, ServiceUnavailable

from context_graph.config import Environment, Settings
from context_graph.domain import Clock, GraphError, Node, PathKey, Relation, Schema
from context_graph.semantic import Embedder
from context_graph.storage.ranking import Ranking


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

    Every node is ``(:Entity:<TypeLabel>)`` with the properties listed in ``PROPERTIES`` plus an
    ``embedding`` vector for semantic search. Relations are real relationship types with an
    optional ``note``. Project scoping uses ``(n)-[:IN_PROJECT]->(:Project)``.

    Attributes:
        settings: Connection settings; re-read on the next connection attempt while no password
            is configured, so a database started mid-session is picked up.
        embedder: Computes the vectors for semantic search; search degrades to keywords only
            when it is unavailable.
        client: Name of the writing client, recorded as provenance.
    """

    PROPERTIES = (
        "id", "type", "title", "summary", "body", "status", "tags", "path",
        "created", "updated", "created_by", "updated_by", "use_count", "last_used",
    )
    SCHEMA = (
        "CREATE CONSTRAINT entity_id IF NOT EXISTS FOR (n:Entity) REQUIRE n.id IS UNIQUE",
        "CREATE INDEX entity_type IF NOT EXISTS FOR (n:Entity) ON (n.type)",
        "CREATE INDEX entity_updated IF NOT EXISTS FOR (n:Entity) ON (n.updated)",
        "CREATE INDEX project_path IF NOT EXISTS FOR (n:Project) ON (n.path_key)",
        "CREATE FULLTEXT INDEX entity_text IF NOT EXISTS FOR (n:Entity) "
        "ON EACH [n.id, n.title, n.summary, n.body, n.tags_text]",
        f"CREATE VECTOR INDEX entity_embedding IF NOT EXISTS FOR (n:Entity) ON n.embedding "
        f"OPTIONS {{indexConfig: {{`vector.dimensions`: {Embedder.DIMENSIONS}, "
        f"`vector.similarity_function`: 'cosine'}}}}",
    )
    PROJECTION = "n {" + ", ".join(f".{p}" for p in PROPERTIES) + "}"
    # Appended to a MATCH binding `n`: one row per node with its properties, relations and projects.
    NODE_RETURN = f"""
    OPTIONAL MATCH (n)-[r]->(m:Entity) WHERE type(r) <> 'IN_PROJECT'
    WITH n, collect(CASE WHEN m IS NULL THEN null ELSE {{type: type(r), target: m.id, note: r.note}} END) AS rels
    OPTIONAL MATCH (n)-[:IN_PROJECT]->(p:Project)
    RETURN {PROJECTION} AS props, rels, collect(p.id) AS projects
    """
    BRIEF = "{.id, .type, .title, .summary, .status, .updated}"
    # Relationships that count towards a node's importance (project membership doesn't).
    DEGREE = "COUNT { (n)-[r]-() WHERE type(r) <> 'IN_PROJECT' }"

    def __init__(self, settings: Settings | None = None, embedder: Embedder | None = None):
        self.settings = settings or Settings.from_environment()
        self.embedder = embedder or Embedder()
        self.client = Environment.client()
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
            self.run(statement)

    def run(self, query: str, **params) -> list[dict]:
        """Runs one auto-committed query.

        Args:
            query: Cypher query.
            **params: Query parameters.

        Returns:
            The rows as dicts.
        """
        with Neo4jErrorTranslator(self.settings):
            records, _, _ = self.driver.execute_query(query, params, database_=self.settings.database)
        return [record.data() for record in records]

    def write(self, work, *args):
        """Runs ``work(tx, *args)`` in one managed write transaction and returns its result."""
        with Neo4jErrorTranslator(self.settings), self.driver.session(database=self.settings.database) as s:
            return s.execute_write(work, *args)

    # ------------------------------------------------------------------ reads

    def get(self, node_id: str) -> Node | None:
        """Returns the node with this id, or None."""
        rows = self.run("MATCH (n:Entity {id: $id})" + self.NODE_RETURN, id=node_id)
        return self._to_node(rows[0]) if rows else None

    def get_many(self, ids: list[str]) -> list[Node]:
        """Returns the existing nodes among ``ids``, in the order given."""
        if not ids:
            return []
        rows = self.run("MATCH (n:Entity) WHERE n.id IN $ids" + self.NODE_RETURN, ids=ids)
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
            order: ``title`` (alphabetical), ``updated`` (most recent first) or ``importance``
                (see ``Ranking.importance``).
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
        # Importance is scored in Python over a wider window of recent candidates.
        fetch = min(max(limit * 5, 50), 500) if order == "importance" else limit
        order_by = "toLower(n.title)" if order == "title" else "n.updated DESC"
        rows = self.run(
            f"MATCH (n:Entity) WHERE {' AND '.join(where)} "
            f"RETURN n {self.BRIEF} AS n, {self.DEGREE} AS degree, coalesce(n.use_count, 0) AS uses "
            f"ORDER BY {order_by} LIMIT $limit",
            type=type, status=status, project=project, limit=fetch,
        )
        if order == "importance":
            now = dt.datetime.now(dt.timezone.utc)
            rows.sort(key=lambda r: -Ranking.importance(r["degree"], r["uses"], r["n"]["updated"] or "", now))
        return [row["n"] for row in rows[:limit]]

    def count(self, type: str | None = None, status: str | None = "active", project: str | None = None,
              include_global: bool = True) -> int:
        """Counts the nodes ``find`` would list without a limit."""
        where = ["($type IS NULL OR n.type = $type)", "($status IS NULL OR n.status = $status)"]
        if project:
            scoped = "EXISTS { (n)-[:IN_PROJECT]->(:Project {id: $project}) }"
            if include_global:
                scoped = f"({scoped} OR NOT EXISTS {{ (n)-[:IN_PROJECT]->() }})"
            where.append(scoped)
        rows = self.run(f"MATCH (n:Entity) WHERE {' AND '.join(where)} RETURN count(n) AS c",
                        type=type, status=status, project=project)
        return rows[0]["c"]

    def counts(self) -> dict[str, int]:
        """Returns the number of nodes per type."""
        rows = self.run("MATCH (n:Entity) RETURN n.type AS type, count(*) AS c ORDER BY type")
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
        outgoing = self.run(f"MATCH (n:Entity {{id: $id}})-[r]->(m:Entity) {tail}", id=node_id)
        incoming = self.run(f"MATCH (m:Entity)-[r]->(n:Entity {{id: $id}}) {tail}", id=node_id)
        return outgoing, incoming

    def search(
        self, query: str, type: str | None = None, project: str | None = None, limit: int = 10
    ) -> list[tuple[float, Node]]:
        """Hybrid search: keyword and semantic rankings fused with reciprocal rank fusion.

        Nodes that belong only to other projects, and inactive nodes, are ranked a few places
        lower (``Ranking.OTHER_PROJECT_PENALTY``, ``Ranking.INACTIVE_PENALTY``).

        Args:
            query: Free text, in any language the embedding model knows; Lucene operators are
                ignored.
            type: Only nodes of this type.
            project: Current project; None ranks all projects alike.
            limit: Maximum number of results.

        Returns:
            ``(score, node)`` pairs, best first.
        """
        window = max(limit * 3, 20)
        keyword = self._keyword_ranking(query, type, window)
        vector = self.embedder.embed_one(query) if query.strip() else None
        semantic = [node_id for node_id, similarity in self._vector_ranking(vector, type, window)
                    if similarity >= Ranking.SEMANTIC_FLOOR] if vector else []
        candidates = list(dict.fromkeys(keyword + semantic))
        if not candidates:
            return []
        rows = self.run(
            "MATCH (n:Entity) WHERE n.id IN $ids RETURN n.id AS id, n.status AS status, "
            "EXISTS { (n)-[:IN_PROJECT]->() } AS scoped, "
            "($project IS NOT NULL AND EXISTS { (n)-[:IN_PROJECT]->(:Project {id: $project}) }) AS here",
            ids=candidates, project=project,
        )
        penalties = {
            row["id"]: (Ranking.OTHER_PROJECT_PENALTY if project and row["scoped"] and not row["here"] else 0)
            + (Ranking.INACTIVE_PENALTY if row["status"] != "active" else 0)
            for row in rows
        }
        fused = Ranking.fuse(keyword, semantic, penalties=penalties)
        best = sorted(fused, key=lambda i: (-fused[i], i))[:limit]
        return [(fused[n.id], n) for n in self.get_many(best)]

    def similar(self, text: str, type: str | None = None, exclude: str | None = None,
                threshold: float = 0.8, limit: int = 3) -> list[tuple[float, dict]]:
        """Finds nodes whose meaning is close to ``text``, e.g. to warn about duplicates.

        Args:
            text: Text to compare, typically ``Embedder.text_for`` of a node.
            type: Only nodes of this type.
            exclude: Id to leave out (the node itself).
            threshold: Minimum cosine similarity.
            limit: Maximum number of matches.

        Returns:
            ``(similarity, brief dict)`` pairs, most similar first; empty without embeddings.
        """
        vector = self.embedder.embed_one(text)
        if vector is None:
            return []
        matches = self._vector_ranking(vector, type, limit + 1, with_brief=True)
        return [(score, brief) for _, score, brief in matches
                if brief["id"] != exclude and score >= threshold][:limit]

    def projects_for_path(self, directory: str) -> list[Node]:
        """Returns the project nodes whose ``path`` contains ``directory``, most specific first."""
        here = PathKey.normalize(directory)
        rows = self.run("MATCH (p:Project) WHERE p.path_key IS NOT NULL RETURN p.id AS id, p.path_key AS k")
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
            records = session.execute_read(self._fetch_tx, query, params or {}, limit)
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

        The embedding is recomputed whenever the title, summary, tags or body change.

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
            node = Node(id=node_id, type=Schema.node_type(type), title=title or node_id,
                        created=Clock.now(), created_by=self.client)
            old_type, old_text = None, None
        else:
            node, old_type = existing, existing.type
            old_text = self.text_of(existing)
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
        node.updated, node.updated_by = Clock.now(), self.client

        text = self.text_of(node)
        embedding = self.embedder.embed_one(text) if text != old_text else ...
        links = [(Schema.relationship(r.type), Schema.node_id(r.target), r.note) for r in relations or []]
        if project:
            Schema.node_id(project)
        self.write(self._upsert_tx, node, old_type, embedding, links, project)
        return self.get(node_id), created

    def link(self, source: str, relation: str, target: str, note: str | None = None) -> bool:
        """Adds a relation between two existing nodes.

        Returns:
            True if it was created, False if it already existed.

        Raises:
            GraphError: If the relation name is invalid or a node is missing.
        """
        result = self.write(self._link_tx, source, Schema.relationship(relation), target, note, True)
        if result is None:
            missing = [i for i in (source, target) if self.get(i) is None]
            raise GraphError(f"node(s) do not exist: {missing}")
        return result

    def unlink(self, source: str, relation: str, target: str) -> bool:
        """Removes a relation. Returns whether one was removed."""
        rows = self.run(
            f"MATCH (:Entity {{id: $a}})-[r:{Schema.relationship(relation)}]->(:Entity {{id: $b}}) "
            "DELETE r RETURN count(*) AS c",
            a=source, b=target,
        )
        return bool(rows and rows[0]["c"])

    def set_project(self, node_id: str, project_id: str, remove: bool = False) -> None:
        """Attaches a node to a project, or detaches it with ``remove=True``."""
        if remove:
            self.run("MATCH (:Entity {id: $n})-[r:IN_PROJECT]->(:Project {id: $p}) DELETE r",
                     n=node_id, p=project_id)
        else:
            self.write(self._scope_tx, node_id, project_id)

    def delete(self, node_id: str) -> int:
        """Deletes a node and all its relations.

        Returns:
            How many incoming relations were removed.

        Raises:
            GraphError: If the node does not exist.
        """
        rows = self.run(
            "MATCH (n:Entity {id: $id}) OPTIONAL MATCH (n)<-[r]-() "
            "WITH n, count(r) AS inc DETACH DELETE n RETURN inc",
            id=node_id,
        )
        if not rows:
            raise GraphError(f"node {node_id!r} does not exist")
        return rows[0]["inc"]

    def touch(self, ids: list[str]) -> None:
        """Records that these nodes were used (returned by a search or read), for importance."""
        if ids:
            self.run("MATCH (n:Entity) WHERE n.id IN $ids "
                     "SET n.use_count = coalesce(n.use_count, 0) + 1, n.last_used = $now",
                     ids=ids, now=Clock.now())

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

    def set_embeddings(self, vectors: dict[str, list[float] | None]) -> None:
        """Stores precomputed embeddings, e.g. from a reindex.

        Args:
            vectors: Embedding (or None to clear it) per node id.
        """
        self.run(
            "UNWIND $rows AS row MATCH (n:Entity {id: row.id}) "
            "SET n.embedding = row.vector, n.embedding_model = CASE WHEN row.vector IS NULL "
            "THEN null ELSE $model END",
            rows=[{"id": i, "vector": v} for i, v in vectors.items()], model=self.embedder.model_name,
        )

    # ------------------------------------------------------------------ backup

    def export_all(self) -> list[dict]:
        """Returns every node as an export dict, sorted by id (embeddings are not exported)."""
        rows = self.run("MATCH (n:Entity)" + self.NODE_RETURN + " ORDER BY props.id")
        return [self._to_node(row).to_dict() for row in rows]

    def import_nodes(self, nodes: list[Node]) -> int:
        """Loads nodes with their relations and projects in one transaction, keeping timestamps.

        Embeddings are computed for the imported nodes when the embedder is available.

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
        vectors = self.embedder.embed([self.text_of(n) for n in nodes]) or [None] * len(nodes)
        self.write(self._import_tx, nodes, vectors)
        return len(nodes)

    # ------------------------------------------------------------------ transaction functions

    def _upsert_tx(self, tx, node: Node, old_type: str | None, embedding, links: list, project: str | None) -> None:
        self._save_tx(tx, node, old_type, embedding)
        missing = [target for rel, target, note in links if self._link_tx(tx, node.id, rel, target, note) is None]
        if missing:
            raise GraphError(f"relation targets do not exist: {missing}. Create them first.")
        if project:
            self._scope_tx(tx, node.id, project)

    def _import_tx(self, tx, nodes: list[Node], vectors: list) -> None:
        for node, vector in zip(nodes, vectors):
            self._save_tx(tx, node, None, vector)
        for node in nodes:
            for relation in node.relations:
                rel = Schema.relationship(relation.type)
                if self._link_tx(tx, node.id, rel, relation.target, relation.note, False) is None:
                    raise GraphError(f"{node.id} -> {relation.target}: target missing from import")
            for project in node.projects:
                self._scope_tx(tx, node.id, project)

    def _save_tx(self, tx, node: Node, old_type: str | None, embedding=...) -> None:
        """Writes the node's properties; ``embedding=...`` keeps the stored vector unchanged."""
        label = Schema.label(node.type)
        remove = f"REMOVE n:{Schema.label(old_type)} " if old_type and old_type != node.type else ""
        props = {
            "type": node.type, "title": node.title, "summary": node.summary, "body": node.body,
            "status": node.status, "tags": node.tags, "tags_text": " ".join(node.tags),
            "path": node.path, "path_key": PathKey.normalize(node.path) if node.path else None,
            "created": node.created, "updated": node.updated,
            "created_by": node.created_by or None, "updated_by": node.updated_by or None,
        }
        if embedding is not ...:
            props["embedding"] = embedding
            props["embedding_model"] = self.embedder.model_name if embedding is not None else None
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
    def _fetch_tx(tx, query: str, params: dict, limit: int) -> list:
        return tx.run(query, params).fetch(limit)

    # ------------------------------------------------------------------ helpers

    def _keyword_ranking(self, query: str, type: str | None, limit: int) -> list[str]:
        lucene = self.lucene_query(query)
        if not lucene:
            return []
        rows = self.run(
            "CALL db.index.fulltext.queryNodes('entity_text', $q) YIELD node, score "
            "WHERE $type IS NULL OR node.type = $type RETURN node.id AS id ORDER BY score DESC LIMIT $limit",
            q=lucene, type=type, limit=limit,
        )
        return [row["id"] for row in rows]

    def _vector_ranking(self, vector: list[float], type: str | None, limit: int,
                        with_brief: bool = False) -> list[tuple]:
        """Nearest neighbours of ``vector`` as ``(id, cosine similarity[, brief])``, best first.

        Neo4j reports cosine scores normalized to ``(1 + cos) / 2``; they are converted back.
        The type filter runs after the approximate search, hence the over-fetch.
        """
        rows = self.run(
            "CALL db.index.vector.queryNodes('entity_embedding', $k, $vector) YIELD node AS n, score "
            f"WHERE $type IS NULL OR n.type = $type RETURN n.id AS id, score, n {self.BRIEF} AS brief "
            "ORDER BY score DESC LIMIT $limit",
            k=limit * 4 if type else limit, vector=vector, type=type, limit=limit,
        )
        if with_brief:
            return [(row["id"], 2 * row["score"] - 1, row["brief"]) for row in rows]
        return [(row["id"], 2 * row["score"] - 1) for row in rows]

    @staticmethod
    def text_of(node: Node) -> str:
        """Returns the text that represents ``node`` in the vector index."""
        return Embedder.text_for(node.title, node.summary, node.tags, node.body)

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
