import pytest

from context_graph.config import Settings
from context_graph.model import (
    GraphError, Node, check_id, check_type, normalize_path, slugify, to_label, to_rel,
)
from context_graph.neo4j_store import _lucene


def test_ids_and_types():
    assert check_id("decision-use-postgres") == "decision-use-postgres"
    for bad in ["Bad Id", "-x", "", "a" * 81, "a_b"]:
        with pytest.raises(GraphError):
            check_id(bad)
    assert check_type("Decision") == "decision"
    with pytest.raises(GraphError):
        check_type("user story")


def test_cypher_identifiers_are_safe():
    assert to_label("user_story") == "UserStory"
    assert to_rel("works_on") == "WORKS_ON"
    for bad in ["WORKS ON", "x]->(y) DETACH DELETE y //", "1abc", "a-b"]:
        with pytest.raises(GraphError):
            to_rel(bad)
    with pytest.raises(GraphError):
        to_rel("in_project")  # system relation
    with pytest.raises(GraphError):
        to_label("x:Admin")


def test_paths_and_slugs():
    assert normalize_path("/c/x/y/") == normalize_path("C:\\x\\y")
    assert slugify("My Cool Project!") == "my-cool-project"


def test_lucene_query_strips_operators():
    assert _lucene('foo:bar "baz" (x)') == "foo OR foo* OR bar OR bar* OR baz OR baz* OR x OR x*"
    assert _lucene("!!") == ""


def test_legacy_markdown():
    node = Node.from_markdown(
        "---\nid: a\ntype: decision\ntitle: A\nrelations:\n- type: part_of\n  target: p\n"
        "created: 2026-10-20T10:56+0200\n---\n\nBody text\n"
    )
    assert node.relations[0].target == "p" and node.body == "Body text"
    assert node.created == "2026-10-20T10:56+0200"


def test_settings_ignore_unresolved_placeholders(monkeypatch, tmp_path):
    monkeypatch.setenv("CONTEXT_GRAPH_HOME", str(tmp_path))
    monkeypatch.setenv("CONTEXT_GRAPH_NEO4J_URI", "${user_config.neo4j_uri}")
    monkeypatch.setenv("CONTEXT_GRAPH_NEO4J_PASSWORD", "")
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_NEO4J_USER", "alice")
    (tmp_path / "neo4j-password").write_text("from-file\n")
    s = Settings.from_env()
    assert (s.uri, s.user, s.password) == ("bolt://127.0.0.1:7688", "alice", "from-file")
