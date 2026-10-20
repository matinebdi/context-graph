import pytest

from context_graph.config import Settings
from context_graph.domain import GraphError, Node, PathKey, Schema
from context_graph.storage import Neo4jStore


class TestSchema:
    def test_ids_and_types(self):
        assert Schema.node_id("decision-use-postgres") == "decision-use-postgres"
        for bad in ["Bad Id", "-x", "", "a" * 81, "a_b"]:
            with pytest.raises(GraphError):
                Schema.node_id(bad)
        assert Schema.node_type("Decision") == "decision"
        with pytest.raises(GraphError):
            Schema.node_type("user story")

    def test_cypher_identifiers_are_safe(self):
        assert Schema.label("user_story") == "UserStory"
        assert Schema.relationship("works_on") == "WORKS_ON"
        for bad in ["WORKS ON", "x]->(y) DETACH DELETE y //", "1abc", "a-b"]:
            with pytest.raises(GraphError):
                Schema.relationship(bad)
        with pytest.raises(GraphError):
            Schema.relationship("in_project")  # system relation
        with pytest.raises(GraphError):
            Schema.label("x:Admin")

    def test_slug(self):
        assert Schema.slug("My Cool Project!") == "my-cool-project"
        assert Schema.slug("!!!") == "node"


class TestPathKey:
    def test_windows_git_bash_and_posix_forms_match(self):
        assert PathKey.normalize("/c/x/y/") == PathKey.normalize("C:\\x\\y")


class TestLuceneQuery:
    def test_strips_operators(self):
        assert Neo4jStore.lucene_query('rotate:jwt "tokens" (x)') == "rotate OR rotate* OR jwt OR tokens OR tokens*"
        assert Neo4jStore.lucene_query("pourquoi la") == "pourquoi OR pourquoi* OR la"  # no "la*" noise
        assert Neo4jStore.lucene_query("!!") == ""


class TestNode:
    def test_legacy_markdown(self):
        node = Node.from_markdown(
            "---\nid: a\ntype: decision\ntitle: A\nrelations:\n- type: part_of\n  target: p\n"
            "created: 2026-10-20T10:56+0200\n---\n\nBody text\n"
        )
        assert node.relations[0].target == "p" and node.body == "Body text"
        assert node.created == "2026-10-20T10:56+0200"


class TestSettings:
    def test_unresolved_placeholders_count_as_unset(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CONTEXT_GRAPH_HOME", str(tmp_path))
        monkeypatch.setenv("CONTEXT_GRAPH_NEO4J_URI", "${user_config.neo4j_uri}")
        monkeypatch.setenv("CONTEXT_GRAPH_NEO4J_PASSWORD", "")
        monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_NEO4J_USER", "alice")
        (tmp_path / "neo4j-password").write_text("from-file\n")
        settings = Settings.from_environment()
        assert (settings.uri, settings.user, settings.password) == ("bolt://127.0.0.1:7688", "alice", "from-file")
        assert settings.is_local
