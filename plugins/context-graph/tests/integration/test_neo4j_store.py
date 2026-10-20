import pytest

from context_graph.domain import GraphError, Node, Relation
from context_graph.rendering import MarkdownRenderer
from context_graph.server import ContextGraphServer


class TestUpsert:
    def test_creates_merges_and_relabels(self, store):
        node, created = store.upsert("d1", type="decision", title="Use Postgres", summary="db choice")
        assert created and node.created and node.type == "decision"
        node, created = store.upsert("d1", append_body="Because of JSONB.", tags=["DB", "infra"])
        assert not created and node.summary == "db choice" and "JSONB" in node.body
        assert node.tags == ["db", "infra"]
        assert store.read_cypher("MATCH (n:Decision) RETURN n.id AS id") == [{"id": "d1"}]
        store.upsert("d1", type="concept")
        labels = store.read_cypher("MATCH (n {id: 'd1'}) RETURN labels(n) AS l")[0]["l"]
        assert sorted(labels) == ["Concept", "Entity"]

    def test_requires_type_and_is_atomic(self, store):
        with pytest.raises(GraphError):
            store.upsert("nope")
        with pytest.raises(GraphError, match="do not exist"):
            store.upsert("a", type="concept", relations=[Relation("depends_on", "missing")])
        assert store.get("a") is None  # rolled back with the failed relation


class TestRelations:
    def test_link_unlink_delete(self, store):
        store.upsert("a", type="concept", title="A")
        store.upsert("b", type="concept", title="B")
        assert store.link("a", "depends_on", "b", note="hard") is True
        assert store.link("a", "DEPENDS_ON", "b") is False
        assert store.get("a").relations[0].to_dict() == {"type": "DEPENDS_ON", "target": "b", "note": "hard"}
        with pytest.raises(GraphError):
            store.link("a", "depends_on", "ghost")
        outgoing, incoming = store.neighbors("b")
        assert outgoing == [] and incoming[0]["other"]["id"] == "a"
        assert store.unlink("a", "depends_on", "b") and not store.unlink("a", "depends_on", "b")
        store.link("a", "uses", "b")
        assert store.delete("b") == 1 and store.get("a").relations == []


class TestProjectsAndSearch:
    def test_scoping_and_ranking(self, store, tmp_path):
        project = store.ensure_project(str(tmp_path / "shop-api"))
        assert project.id == "shop-api"
        assert store.ensure_project(str(tmp_path / "shop-api" / "src")).id == "shop-api"
        assert store.ensure_project(str(tmp_path / "other" / "shop-api")).id == "shop-api-2"

        store.upsert("pref-tests", type="preference", title="Write tests first", summary="TDD please")
        store.upsert("dec-auth", type="decision", title="JWT auth", body="Tokens rotate hourly", project="shop-api")
        store.upsert("dec-auth-2", type="decision", title="Session auth", project="shop-api-2")

        # project members + global nodes (preferences, projects), never another project's nodes
        assert {n["id"] for n in store.find(project="shop-api")} == {"pref-tests", "dec-auth", "shop-api", "shop-api-2"}
        assert {n["id"] for n in store.find(project="shop-api", include_global=False)} == {"dec-auth"}

        assert [n.id for _, n in store.search("rotate")] == ["dec-auth"]
        assert [n.id for _, n in store.search("rot")] == ["dec-auth"]  # prefix match
        assert [n.id for _, n in store.search("auth", type="decision", project="shop-api-2")][0] == "dec-auth-2"
        assert store.get("dec-auth").projects == ["shop-api"]

        store.set_project("dec-auth", "shop-api", remove=True)
        assert store.get("dec-auth").projects == []


class TestReadOnlyCypher:
    def test_rejects_writes(self, store):
        with pytest.raises(GraphError):
            store.read_cypher("CREATE (n:Entity {id: 'x'})")
        assert store.get("x") is None


class TestBackup:
    def test_export_import_roundtrip(self, store):
        store.upsert("p", type="project", title="P", path="/tmp/p")
        store.upsert("t", type="task", title="T", relations=[Relation("part_of", "p")], project="p")
        dump = store.export_all()
        store.run("MATCH (n) DETACH DELETE n")
        assert store.import_nodes([Node.from_dict(d) for d in dump]) == 2
        assert store.export_all() == dump


class TestRendering:
    def test_overview_and_node_page(self, store, tmp_path):
        project = store.ensure_project(str(tmp_path / "web"))
        store.upsert("me", type="person", title="Me", relations=[Relation("works_on", project.id)])
        store.upsert("task-x", type="task", title="Ship it", project=project.id)
        renderer = MarkdownRenderer(store)
        overview = renderer.overview(project.id)
        assert "People" in overview and "Open tasks in web" in overview and "task-x" in overview
        page = renderer.node(project.id, depth=2)
        assert "WORKS_ON" in page and "In this project" in page and "task-x" in page


class TestServerTools:
    def test_scope_auto_and_errors(self, store, tmp_path, monkeypatch):
        monkeypatch.setenv("CONTEXT_GRAPH_PROJECT_DIR", str(tmp_path / "api"))
        server = ContextGraphServer(store)
        assert "(global)" in server.upsert_node("pref-x", type="preference", title="X")
        assert "in project api" in server.upsert_node("dec-y", type="decision", title="Y")
        assert "in project api" in server.upsert_node("dec-y", scope="auto", summary="still scoped")
        with pytest.raises(GraphError, match="do not exist"):
            server.link("dec-y", "depends_on", "ghost")
        assert server.log_session("did things", related=["dec-y"]).startswith("Logged `session-")
