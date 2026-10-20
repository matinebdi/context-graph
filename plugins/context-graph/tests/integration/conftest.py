import os
import shutil
import subprocess
import time

import pytest

from context_graph.config import Settings
from context_graph.domain import GraphError
from context_graph.storage import Neo4jStore


class ThrowawayNeo4j:
    """A disposable Neo4j for integration tests; never the user's database.

    Uses ``CONTEXT_GRAPH_TEST_URI`` / ``CONTEXT_GRAPH_TEST_PASSWORD`` when set (that database is
    wiped), otherwise starts a container on port 17687 and removes it afterwards.

    Attributes:
        settings: Connection settings of the test database.
    """

    CONTAINER = "context-graph-test-neo4j"
    PASSWORD = "test-password-123"
    IMAGE = "neo4j:5.26-community"

    def __init__(self):
        uri = os.environ.get("CONTEXT_GRAPH_TEST_URI")
        self._started = not uri
        password = os.environ.get("CONTEXT_GRAPH_TEST_PASSWORD", self.PASSWORD)
        self.settings = Settings(uri or "bolt://127.0.0.1:17687", "neo4j", password, "neo4j")

    @staticmethod
    def docker_available() -> bool:
        """Whether a Docker daemon answers."""
        return shutil.which("docker") is not None and subprocess.run(
            ["docker", "info"], capture_output=True).returncode == 0

    def start(self, timeout: int = 180) -> None:
        """Starts the container if needed, waits for Bolt and creates the schema."""
        if self._started:
            subprocess.run(["docker", "rm", "-f", self.CONTAINER], capture_output=True)
            subprocess.run(
                ["docker", "run", "-d", "--name", self.CONTAINER, "-p", "127.0.0.1:17687:7687",
                 "-e", f"NEO4J_AUTH=neo4j/{self.PASSWORD}", self.IMAGE],
                check=True, capture_output=True,
            )
        store = Neo4jStore(self.settings)
        deadline = time.time() + timeout
        while True:
            try:
                store.ping()
                break
            except GraphError:
                if time.time() > deadline:
                    raise
                time.sleep(2)
        store.init_schema()
        store.close()

    def stop(self) -> None:
        """Removes the container if this object started it."""
        if self._started:
            subprocess.run(["docker", "rm", "-f", self.CONTAINER], capture_output=True)


@pytest.fixture(scope="session")
def neo4j():
    database = ThrowawayNeo4j()
    if database._started and not ThrowawayNeo4j.docker_available():
        pytest.skip("Docker not available for Neo4j integration tests")
    database.start()
    yield database
    database.stop()


@pytest.fixture
def store(neo4j):
    graph = Neo4jStore(neo4j.settings)
    graph._run("MATCH (n) DETACH DELETE n")
    yield graph
    graph.close()
