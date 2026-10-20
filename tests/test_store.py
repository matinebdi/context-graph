import pytest

from context_graph.render import render_node, render_overview
from context_graph.store import GraphError, GraphStore, Relation, normalize_path


@pytest.fixture
def store(tmp_path):
    return GraphStore(tmp_path, autocommit=False)


def test_upsert_creates_then_merges(store):
    node, created = store.upsert("alice", type="person", title="Alice", summary="A user")
    assert created and node.created
    node, created = store.upsert("alice", append_body="Likes tea.")
    assert not created
    assert node.summary == "A user"
    assert "Likes tea." in node.body
    assert store.get("alice").title == "Alice"


def test_roundtrip_preserves_fields(store):
    store.upsert("proj", type="project", title="Proj", path="C:/code/proj", tags=["B", "a"])
    store.upsert("me", type="person", title="Me", relations=[Relation("works_on", "proj", "lead")])
    me = store.get("me")
    assert me.relations[0].to_dict() == {"type": "works_on", "target": "proj", "note": "lead"}
    assert store.get("proj").tags == ["a", "b"]


def test_validation(store):
    with pytest.raises(GraphError):
        store.upsert("Bad Id", type="person")
    with pytest.raises(GraphError):
        store.upsert("x", type="alien")
    with pytest.raises(GraphError):
        store.upsert("missing")  # no type when creating


def test_link_unlink_delete(store):
    store.upsert("a", type="concept", title="A")
    store.upsert("b", type="concept", title="B")
    assert store.link("a", "depends_on", "b")
    assert not store.link("a", "depends_on", "b")  # deduplicated
    with pytest.raises(GraphError):
        store.link("a", "depends_on", "nope")
    assert "Incoming" in render_node(store, "b")
    assert store.delete("b") == 1
    assert store.get("a").relations == []
    assert store.unlink("a", "depends_on", "b") is False


def test_search_and_overview(store):
    store.upsert("pref-short", type="preference", title="Short answers", summary="Be concise")
    store.upsert("graph", type="project", title="Context graph", summary="MCP memory")
    assert store.search("concise")[0][1].id == "pref-short"
    assert store.search("graph", type="preference") == []
    ov = render_overview(store)
    assert "Preferences" in ov and "pref-short" in ov


def test_projects_for_path(store):
    store.upsert("outer", type="project", title="Outer", path=r"C:\Users\me\code")
    store.upsert("inner", type="project", title="Inner", path="C:/Users/me/code/inner")
    hits = store.projects_for_path("/c/Users/me/code/inner/src")
    assert [n.id for n in hits] == ["inner", "outer"]
    assert store.projects_for_path("C:/Users/me/codex") == []
    assert normalize_path("/c/x/y/") == normalize_path("C:\\x\\y")
