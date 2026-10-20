"""Integration tests against a throwaway Neo4j container (never the user's database).

Skipped when Docker is unavailable. Set CONTEXT_GRAPH_TEST_URI / CONTEXT_GRAPH_TEST_PASSWORD to
use an existing disposable database instead; it will be wiped.
"""

import os
import shutil
import subprocess
import time

import pytest

from context_graph.config import Settings
from context_graph.model import GraphError, Node, Relation
from context_graph.neo4j_store import Neo4jStore
from context_graph.render import render_node, render_overview

CONTAINER = "context-graph-test-neo4j"
PASSWORD = "test-password-123"


def _docker_ok() -> bool:
    return shutil.which("docker") is not None and subprocess.run(
        ["docker", "info"], capture_output=True).returncode == 0


@pytest.fixture(scope="session")
def settings():
    uri = os.environ.get("CONTEXT_GRAPH_TEST_URI")
    started = False
    if uri:
        s = Settings(uri, "neo4j", os.environ.get("CONTEXT_GRAPH_TEST_PASSWORD", PASSWORD), "neo4j")
    else:
        if not _docker_ok():
            pytest.skip("Docker not available for Neo4j integration tests")
        subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True)
        subprocess.run(
            ["docker", "run", "-d", "--name", CONTAINER, "-p", "127.0.0.1:17687:7687",
             "-e", f"NEO4J_AUTH=neo4j/{PASSWORD}", "neo4j:5.26-community"],
            check=True, capture_output=True,
        )
        started = True
        s = Settings("bolt://127.0.0.1:17687", "neo4j", PASSWORD, "neo4j")
    probe = Neo4jStore(s)
    deadline = time.time() + 180
    while True:
        try:
            probe.ping()
            break
        except GraphError:
            if time.time() > deadline:
                raise
            time.sleep(2)
    probe.init_schema()
    probe.close()
    yield s
    if started:
        subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True)


@pytest.fixture
def store(settings):
    st = Neo4jStore(settings)
    st._run("MATCH (n) DETACH DELETE n")
    yield st
    st.close()


def test_upsert_creates_merges_and_labels(store):
    node, created = store.upsert("d1", type="decision", title="Use Postgres", summary="db choice")
    assert created and node.created and node.type == "decision"
    node, created = store.upsert("d1", append_body="Because of JSONB.", tags=["DB", "infra"])
    assert not created and node.summary == "db choice" and "JSONB" in node.body
    assert node.tags == ["db", "infra"]
    assert store.read_cypher("MATCH (n:Decision) RETURN n.id AS id") == [{"id": "d1"}]
    store.upsert("d1", type="concept")
    labels = store.read_cypher("MATCH (n {id: 'd1'}) RETURN labels(n) AS l")[0]["l"]
    assert sorted(labels) == ["Concept", "Entity"]


def test_upsert_requires_type_and_is_atomic(store):
    with pytest.raises(GraphError):
        store.upsert("nope")
    with pytest.raises(GraphError, match="do not exist"):
        store.upsert("a", type="concept", relations=[Relation("depends_on", "missing")])
    assert store.get("a") is None  # rolled back with the failed relation


def test_links(store):
    store.upsert("a", type="concept", title="A")
    store.upsert("b", type="concept", title="B")
    assert store.link("a", "depends_on", "b", note="hard") is True
    assert store.link("a", "DEPENDS_ON", "b") is False
    assert store.get("a").relations[0].to_dict() == {"type": "DEPENDS_ON", "target": "b", "note": "hard"}
    with pytest.raises(GraphError):
        store.link("a", "depends_on", "ghost")
    out, inc = store.neighbors("b")
    assert out == [] and inc[0]["other"]["id"] == "a"
    assert store.unlink("a", "depends_on", "b") and not store.unlink("a", "depends_on", "b")
    store.link("a", "uses", "b")
    assert store.delete("b") == 1 and store.get("a").relations == []


def test_projects_scope_and_search(store, tmp_path):
    proj = store.ensure_project(str(tmp_path / "shop-api"))
    assert proj.id == "shop-api" and store.ensure_project(str(tmp_path / "shop-api" / "src")).id == "shop-api"
    other = store.ensure_project(str(tmp_path / "other" / "shop-api"))
    assert other.id == "shop-api-2"

    store.upsert("pref-tests", type="preference", title="Write tests first", summary="TDD please")
    store.upsert("dec-auth", type="decision", title="JWT auth", body="Tokens rotate hourly",
                 project="shop-api")
    store.upsert("dec-auth-2", type="decision", title="Session auth", project="shop-api-2")

    scoped = {n["id"] for n in store.find(project="shop-api")}
    # project members + global nodes (preferences, projects), never another project's nodes
    assert scoped == {"pref-tests", "dec-auth", "shop-api", "shop-api-2"}
    only = {n["id"] for n in store.find(project="shop-api", include_global=False)}
    assert only == {"dec-auth"}

    assert [n.id for _, n in store.search("rotate")] == ["dec-auth"]
    assert [n.id for _, n in store.search("rot")] == ["dec-auth"]  # prefix match
    ranked = [n.id for _, n in store.search("auth", type="decision", project="shop-api-2")]
    assert ranked[0] == "dec-auth-2"
    assert store.get("dec-auth").projects == ["shop-api"]

    store.set_project("dec-auth", "shop-api", remove=True)
    assert store.get("dec-auth").projects == []


def test_read_cypher_rejects_writes(store):
    with pytest.raises(GraphError):
        store.read_cypher("CREATE (n:Entity {id: 'x'})")
    assert store.get("x") is None


def test_export_import_roundtrip(store):
    store.upsert("p", type="project", title="P", path="/tmp/p")
    store.upsert("t", type="task", title="T", relations=[Relation("part_of", "p")], project="p")
    dump = store.export_all()
    store._run("MATCH (n) DETACH DELETE n")
    assert store.import_nodes([Node.from_dict(d) for d in dump]) == 2
    assert store.export_all() == dump


def test_render(store, tmp_path):
    proj = store.ensure_project(str(tmp_path / "web"))
    store.upsert("me", type="person", title="Me", relations=[Relation("works_on", proj.id)])
    store.upsert("task-x", type="task", title="Ship it", project=proj.id)
    ov = render_overview(store, proj.id)
    assert "People" in ov and "Open tasks in web" in ov and "task-x" in ov
    page = render_node(store, proj.id, depth=2)
    assert "WORKS_ON" in page and "In this project" in page and "task-x" in page
