import json
import sys
from abc import ABC, abstractmethod
from importlib import resources
from pathlib import Path

from context_graph.domain import GraphError


class PythonRuntime:
    """The interpreter this package runs under, used to launch it from client configs.

    Configs launch ``<interpreter> -m context_graph.<module>`` with an absolute path. That works
    where generated console-script launchers are blocked (Windows Smart App Control) and does not
    depend on the client's PATH.
    """

    REPOSITORY = "matinebdi/context-graph"
    GIT_SOURCE = f"git+https://github.com/{REPOSITORY}#subdirectory=plugins/context-graph"
    # Folders of uv's cache, whose environments uv may delete at any time.
    EPHEMERAL_PARTS = frozenset({"archive-v0", "builds-v0"})

    @classmethod
    def interpreter(cls) -> str:
        """Returns the absolute path of the running interpreter.

        Raises:
            GraphError: If it lives in a temporary uvx environment that configs must not reference.
        """
        executable = Path(sys.executable).resolve()
        if cls.EPHEMERAL_PARTS & set(executable.parts):
            raise GraphError(
                "this is a temporary uvx environment, which uv may delete later. Install the package "
                "first and rerun the command from it:\n"
                f'  uv tool install "{cls.GIT_SOURCE}"\n'
                "  context-graph install <client>"
            )
        return str(executable)

    @staticmethod
    def shell_path(path: str) -> str:
        """Formats a path for a hook command run by bash, cmd or PowerShell alike.

        Args:
            path: Absolute path.

        Returns:
            The path with forward slashes, quoted only if it contains spaces.
        """
        posix = Path(path).as_posix()
        return f'"{posix}"' if " " in posix else posix


class JsonConfig:
    """A client's JSON config file, merged in place.

    Attributes:
        path: Location of the file.
    """

    def __init__(self, path: Path):
        self.path = path

    def load(self, default: dict) -> dict:
        """Reads the file.

        Args:
            default: Returned (as a copy) when the file is missing or empty.

        Returns:
            The parsed object.

        Raises:
            GraphError: If the file is not a JSON object; it is never overwritten then.
        """
        if not self.path.exists() or not self.path.read_text(encoding="utf-8").strip():
            return json.loads(json.dumps(default))
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise GraphError(f"{self.path} is not valid JSON ({exc}); fix or remove it, then retry") from exc
        if not isinstance(data, dict):
            raise GraphError(f"{self.path} does not contain a JSON object")
        return data

    def save(self, data: dict) -> None:
        """Writes ``data`` as indented JSON, creating parent folders."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


class ClientIntegration(ABC):
    """Wires the context graph into one AI client for one project.

    Implementations merge their entries into the client's own files, never clobbering other
    servers, hooks or rules, and must be idempotent; ``uninstall`` removes exactly what
    ``install`` added.

    Attributes:
        project: Project root directory.
    """

    NAME: str = ""

    def __init__(self, project: Path):
        self.project = project.resolve()

    @abstractmethod
    def install(self) -> list[Path]:
        """Adds the integration. Returns the files written."""

    @abstractmethod
    def uninstall(self) -> list[Path]:
        """Removes the integration. Returns the files changed."""

    @abstractmethod
    def next_steps(self) -> str:
        """Returns what the user still has to do after ``install``."""

    @staticmethod
    def agent_instructions() -> str:
        """Returns the shared usage policy for agents (``assets/agent-instructions.md``)."""
        asset = resources.files("context_graph").joinpath("assets/agent-instructions.md")
        return asset.read_text(encoding="utf-8")
