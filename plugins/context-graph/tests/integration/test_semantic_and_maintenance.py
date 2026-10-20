from context_graph.domain import Relation
from context_graph.rendering import MarkdownRenderer
from context_graph.server import ContextGraphServer
from context_graph.storage import GraphMaintenance


class TestSemanticSearch:
    def test_vector_path_finds_what_keywords_miss(self, store):
        store.upsert("auth", type="concept", title="auth tokens", summary="rotate hourly")
        store.upsert("bread", type="concept", title="sourdough", summary="baking notes")
        assert store._keyword_ranking("authtoken", None, 10) == []  # no keyword match
        assert [n.id for _, n in store.search("authtoken")] == ["auth"]  # vector match, bread below the floor

    def test_embeddings_follow_text_changes(self, store):
        store.upsert("n1", type="concept", title="alpha")
        before = store.run("MATCH (n {id: 'n1'}) RETURN n.embedding AS v")[0]["v"]
        store.upsert("n1", status="done")  # text unchanged: vector kept
        assert store.run("MATCH (n {id: 'n1'}) RETURN n.embedding AS v")[0]["v"] == before
        store.upsert("n1", summary="completely different words")
        assert store.run("MATCH (n {id: 'n1'}) RETURN n.embedding AS v")[0]["v"] != before

    def test_reindex_fills_missing_vectors(self, store):
        store.upsert("n1", type="concept", title="alpha")
        store.run("MATCH (n) REMOVE n.embedding")
        maintenance = GraphMaintenance(store)
        assert maintenance.review().missing_embeddings == 1
        assert maintenance.reindex() == 1 and maintenance.reindex() == 0


class TestProvenanceAndUsage:
    def test_created_and_updated_by(self, store, monkeypatch):
        store.client = "claude-code"
        store.upsert("n1", type="concept", title="A")
        store.client = "cursor"
        node, _ = store.upsert("n1", summary="edited")
        assert (node.created_by, node.updated_by) == ("claude-code", "cursor")
        assert "by: claude-code → cursor" in MarkdownRenderer(store).node("n1")

    def test_importance_prefers_connected_and_used_nodes(self, store):
        for i in range(4):
            store.upsert(f"d{i}", type="decision", title=f"decision {i}")
        store.upsert("hub", type="decision", title="central decision",
                     relations=[Relation("relates_to", f"d{i}") for i in range(3)])
        store.touch(["d3"] * 1)
        store.touch(["d3"])
        ranked = [n["id"] for n in store.find(type="decision", order="importance")]
        assert ranked[0] == "hub" and ranked.index("d3") < ranked.index("d0")


class TestDuplicatesAndMerge:
    def test_server_warns_about_similar_nodes(self, store, tmp_path, monkeypatch):
        monkeypatch.setenv("CONTEXT_GRAPH_PROJECT_DIR", str(tmp_path / "api"))
        server = ContextGraphServer(store)
        server.upsert_node("decision-postgres", type="decision", title="use postgres database for storage")
        reply = server.upsert_node("decision-pg", type="decision", title="use postgres database for the storage")
        assert "Similar existing decision nodes: `decision-postgres`" in reply

    def test_merge_moves_everything_and_deletes_the_duplicate(self, store, tmp_path):
        project = store.ensure_project(str(tmp_path / "api")).id
        store.upsert("me", type="person", title="Me")
        store.upsert("keep", type="decision", title="Postgres", tags=["db"])
        store.upsert("dup", type="decision", title="PostgreSQL", summary="same thing", tags=["sql"],
                     relations=[Relation("part_of", project)], project=project)
        store.link("me", "decided", "dup")
        result = GraphMaintenance(store).merge("keep", "dup")
        assert "moved 2 relation(s)" in result
        kept = store.get("keep")
        assert store.get("dup") is None
        assert kept.tags == ["db", "sql"] and project in kept.projects and "Merged from `dup`" in kept.body
        assert {r.target for r in kept.relations} == {project}
        assert store.neighbors("keep")[1][0]["other"]["id"] == "me"


class TestReview:
    def test_finds_each_kind_of_problem(self, store):
        store.upsert("lonely", type="concept", title="unlinked idea")
        store.upsert("task-old", type="task", title="old task")
        store.run("MATCH (n {id: 'task-old'}) SET n.updated = '2020-01-01T00:00:00Z'")
        store.upsert("old-way", type="decision", title="old way", status="superseded")
        store.upsert("feature", type="concept", title="feature", relations=[Relation("depends_on", "old-way")])
        store.upsert("dup-a", type="concept", title="graph database migration plan",
                     relations=[Relation("relates_to", "feature")])
        store.upsert("dup-b", type="concept", title="graph database migration plans",
                     relations=[Relation("relates_to", "feature")])
        report = GraphMaintenance(store).review(stale_days=30)
        assert {n["id"] for n in report.orphans} >= {"lonely", "task-old"}
        assert [t["id"] for t in report.stale_tasks] == ["task-old"]
        assert report.superseded_in_use == [("old-way", "feature")]
        assert [(a, b) for _, a, b in report.duplicates] == [("dup-a", "dup-b")]
        assert "Likely duplicates" in report.to_markdown()


class TestOverviewBudget:
    def test_budget_truncates_and_counts_the_rest(self, store, tmp_path):
        project = store.ensure_project(str(tmp_path / "web")).id
        for i in range(30):
            store.upsert(f"pref-{i:02d}", type="preference", title=f"preference number {i}",
                         summary="a reasonably long one-line summary to take up some space")
        overview = MarkdownRenderer(store).overview(project, budget=1200)
        assert len(overview) <= 1200
        assert "more (search_graph to find them)" in overview
        full = MarkdownRenderer(store).overview(project)
        assert full.count("- **pref-") == 25 and "…and 5 more" in full
