import os
import subprocess
import sys
import time
from importlib import resources

from context_graph.config import LocalCredentials, Settings
from context_graph.domain import GraphError
from context_graph.storage import Neo4jStore


class LocalDatabase:
    """The bundled Neo4j container, managed with Docker Compose.

    Attributes:
        credentials: Holder of the generated password.
    """

    def __init__(self, credentials: LocalCredentials | None = None):
        self.credentials = credentials or LocalCredentials()

    @property
    def compose_file(self) -> str:
        """Path of ``assets/compose.yml`` inside the installed package."""
        return str(resources.files("context_graph").joinpath("assets/compose.yml"))

    def up(self, timeout: int = 180) -> int:
        """Starts the container, waits for Bolt and creates the schema.

        Args:
            timeout: Seconds to wait for Neo4j to accept connections.

        Returns:
            A process exit code.
        """
        if self._compose("up", "-d") != 0:
            print("docker compose failed. Is Docker installed and running?", file=sys.stderr)
            return 1
        store = Neo4jStore(Settings.from_environment())
        deadline = time.time() + timeout
        print("Waiting for Neo4j to accept connections", end="", flush=True)
        while True:
            try:
                store.ping()
                break
            except GraphError as exc:
                if "credentials" in str(exc):
                    print(f"\n{exc} The data volume was probably created with another password; restore "
                          f"it in {self.credentials.password_file} or remove the volume "
                          "`context-graph_neo4j-data`.", file=sys.stderr)
                    return 1
                if time.time() > deadline:
                    print(f"\nTimed out: {exc}", file=sys.stderr)
                    return 1
                print(".", end="", flush=True)
                time.sleep(3)
        store.init_schema()
        store.close()
        port = os.environ.get("CONTEXT_GRAPH_HTTP_PORT", "7475")
        print(f"\nNeo4j is up at {store.settings.uri}; schema ready.")
        print(f"Browser: http://127.0.0.1:{port}  (user neo4j, password in {self.credentials.password_file})")
        return 0

    def down(self) -> int:
        """Stops the container; the data volume is kept. Returns a process exit code."""
        return self._compose("stop")

    def _compose(self, *args: str) -> int:
        env = {**os.environ, "CONTEXT_GRAPH_NEO4J_PASSWORD": self.credentials.ensure()}
        return subprocess.run(["docker", "compose", "-f", self.compose_file, *args], env=env).returncode
