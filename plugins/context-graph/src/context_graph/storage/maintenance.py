import datetime as dt
from dataclasses import dataclass, field

from context_graph.domain import Clock, GraphError, Schema
from context_graph.storage.neo4j_store import Neo4jStore


@dataclass
class ReviewReport:
    """Findings of a graph review.

    Attributes:
        duplicates: ``(similarity, id_a, id_b)`` for same-type nodes that look alike.
        orphans: Brief dicts of nodes without any relation.
        stale_tasks: Brief dicts of active tasks not updated within the stale period.
        superseded_in_use: ``(superseded id, active dependent id)`` pairs.
        missing_embeddings: Number of nodes without a vector (run ``reindex``).
    """

    duplicates: list[tuple[float, str, str]] = field(default_factory=list)
    orphans: list[dict] = field(default_factory=list)
    stale_tasks: list[dict] = field(default_factory=list)
    superseded_in_use: list[tuple[str, str]] = field(default_factory=list)
    missing_embeddings: int = 0

    @property
    def is_clean(self) -> bool:
        """Whether nothing was found."""
        return not (self.duplicates or self.orphans or self.stale_tasks
                    or self.superseded_in_use or self.missing_embeddings)

    def to_markdown(self) -> str:
        """Renders the findings as a markdown checklist."""
        if self.is_clean:
            return "The graph looks clean: no duplicates, orphans, stale tasks or outdated dependencies."
        out = ["# Graph review"]
        if self.duplicates:
            out.append("\n## Likely duplicates (merge with merge_nodes)")
            out.extend(f"- `{a}` ≈ `{b}` ({score:.2f})" for score, a, b in self.duplicates)
        if self.superseded_in_use:
            out.append("\n## Superseded nodes still depended on")
            out.extend(f"- `{dependent}` depends on superseded `{old}`" for old, dependent in self.superseded_in_use)
        if self.stale_tasks:
            out.append("\n## Stale open tasks (close, update or archive)")
            out.extend(f"- `{t['id']}`: {t['title']} (updated {(t['updated'] or '')[:10]})" for t in self.stale_tasks)
        if self.orphans:
            out.append("\n## Orphans (link them or archive them)")
            out.extend(f"- `{n['id']}` ({n['type']}): {n['title']}" for n in self.orphans)
        if self.missing_embeddings:
            out.append(f"\n{self.missing_embeddings} node(s) have no embedding: run `context-graph reindex`.")
        return "\n".join(out)


class GraphMaintenance:
    """Keeps the graph clean: merging duplicates, reviewing quality, rebuilding embeddings.

    Attributes:
        store: Store to maintain.
    """

    # Cosine similarity above which two same-type nodes are reported as likely duplicates.
    DUPLICATE_THRESHOLD = 0.85
    REVIEW_SCAN_LIMIT = 500

    def __init__(self, store: Neo4jStore):
        self.store = store

    def merge(self, keep: str, remove: str) -> str:
        """Merges ``remove`` into ``keep`` and deletes ``remove``.

        Relations (both directions) and project links move to ``keep``; tags are united; the
        removed node's summary and body are appended to the kept node's body.

        Args:
            keep: Id of the node that survives.
            remove: Id of the duplicate.

        Returns:
            A one-line summary of what moved.

        Raises:
            GraphError: If either node is missing or both ids are the same.
        """
        if keep == remove:
            raise GraphError("cannot merge a node into itself")
        kept, removed = self.store.get(keep), self.store.get(remove)
        missing = [i for i, n in ((keep, kept), (remove, removed)) if n is None]
        if missing:
            raise GraphError(f"node(s) do not exist: {missing}")
        outgoing, incoming = self.store.neighbors(remove)
        note = "\n\n".join(part for part in (removed.summary, removed.body) if part)
        moved = self.store.write(self._merge_tx, keep, remove, outgoing, incoming, removed.projects)
        self.store.upsert(
            keep,
            tags=sorted(set(kept.tags) | set(removed.tags)),
            append_body=f"Merged from `{remove}` ({removed.title}). {note}".strip(),
        )
        return f"Merged `{remove}` into `{keep}`: moved {moved} relation(s) and {len(removed.projects)} project link(s)."

    def review(self, stale_days: int = 30) -> ReviewReport:
        """Looks for duplicates, orphans, stale tasks and superseded nodes still in use.

        Args:
            stale_days: Age after which an open task counts as stale.

        Returns:
            The findings.
        """
        report = ReviewReport()
        run = self.store.run
        report.orphans = [r["n"] for r in run(
            f"MATCH (n:Entity) WHERE NOT EXISTS {{ (n)-[r]-() WHERE type(r) <> 'IN_PROJECT' }} "
            f"AND n.type <> 'project' RETURN n {self.store.BRIEF} AS n ORDER BY n.updated LIMIT 50")]
        cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=stale_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        report.stale_tasks = [r["n"] for r in run(
            f"MATCH (n:Task {{status: 'active'}}) WHERE n.updated < $cutoff "
            f"RETURN n {self.store.BRIEF} AS n ORDER BY n.updated LIMIT 50", cutoff=cutoff)]
        report.superseded_in_use = [(r["old"], r["dependent"]) for r in run(
            "MATCH (d:Entity {status: 'active'})-[r]->(old:Entity {status: 'superseded'}) "
            "WHERE type(r) IN ['DEPENDS_ON', 'USES', 'PART_OF'] RETURN old.id AS old, d.id AS dependent LIMIT 50")]
        report.missing_embeddings = run("MATCH (n:Entity) WHERE n.embedding IS NULL RETURN count(n) AS c")[0]["c"]
        report.duplicates = self._duplicates()
        return report

    def reindex(self, force: bool = False) -> int:
        """Computes embeddings for nodes that lack one, or for every node with ``force``.

        Nodes embedded with a different model are always recomputed.

        Returns:
            The number of nodes embedded.

        Raises:
            GraphError: If the embedder is unavailable.
        """
        if not self.store.embedder.available:
            raise GraphError("the embedding model is unavailable; see the messages above")
        rows = self.store.run(
            "MATCH (n:Entity) WHERE $force OR n.embedding IS NULL OR n.embedding_model <> $model "
            "RETURN n.id AS id, n.title AS title, coalesce(n.summary, '') AS summary, "
            "coalesce(n.tags, []) AS tags, coalesce(n.body, '') AS body",
            force=force, model=self.store.embedder.model_name,
        )
        for start in range(0, len(rows), 64):
            batch = rows[start:start + 64]
            texts = [self.store.embedder.text_for(r["title"], r["summary"], r["tags"], r["body"]) for r in batch]
            vectors = self.store.embedder.embed(texts)
            self.store.set_embeddings({r["id"]: v for r, v in zip(batch, vectors)})
        return len(rows)

    def _duplicates(self) -> list[tuple[float, str, str]]:
        rows = self.store.run(
            "MATCH (n:Entity) WHERE n.embedding IS NOT NULL AND n.status = 'active' "
            "RETURN n.id AS id, n.type AS type, n.embedding AS vector ORDER BY n.updated DESC LIMIT $limit",
            limit=self.REVIEW_SCAN_LIMIT,
        )
        pairs: dict[tuple[str, str], float] = {}
        for row in rows:
            for other, score in self.store._vector_ranking(row["vector"], row["type"], 3):
                if other != row["id"] and score >= self.DUPLICATE_THRESHOLD:
                    pairs[tuple(sorted((row["id"], other)))] = score
        return sorted(((score, a, b) for (a, b), score in pairs.items()), reverse=True)[:30]

    @staticmethod
    def _merge_tx(tx, keep: str, remove: str, outgoing: list[dict], incoming: list[dict], projects: list[str]) -> int:
        moved = 0
        for relation in outgoing:
            target = relation["other"]["id"]
            if target != keep:
                rel = Schema.relationship(relation["rel"])
                tx.run(f"MATCH (a:Entity {{id: $a}}), (b:Entity {{id: $b}}) MERGE (a)-[r:{rel}]->(b) "
                       "SET r.note = coalesce(r.note, $note)", a=keep, b=target, note=relation["note"])
                moved += 1
        for relation in incoming:
            source = relation["other"]["id"]
            if source != keep:
                rel = Schema.relationship(relation["rel"])
                tx.run(f"MATCH (a:Entity {{id: $a}}), (b:Entity {{id: $b}}) MERGE (a)-[r:{rel}]->(b) "
                       "SET r.note = coalesce(r.note, $note)", a=source, b=keep, note=relation["note"])
                moved += 1
        for project in projects:
            tx.run("MATCH (n:Entity {id: $n}), (p:Project {id: $p}) WHERE n <> p MERGE (n)-[:IN_PROJECT]->(p)",
                   n=keep, p=project)
        # Nodes that belonged to a removed project move to the kept one.
        tx.run("MATCH (m:Entity)-[r:IN_PROJECT]->(:Entity {id: $remove}), (k:Entity {id: $keep}) "
               "WHERE m <> k MERGE (m)-[:IN_PROJECT]->(k)", remove=remove, keep=keep)
        tx.run("MATCH (n:Entity {id: $id}) DETACH DELETE n", id=remove)
        tx.run("MATCH (n:Entity {id: $id}) SET n.updated = $now", id=keep, now=Clock.now())
        return moved
