import os
import secrets
from dataclasses import dataclass
from pathlib import Path


class Environment:
    """Reads configuration from environment variables.

    An unset plugin option can reach the process as a literal ``${user_config.x}`` placeholder,
    so such values count as unset.
    """

    @staticmethod
    def get(*names: str, default: str = "") -> str:
        """Returns the first variable among ``names`` that holds a real value.

        Args:
            *names: Variable names, in order of precedence.
            default: Value returned when none of them is set.

        Returns:
            The value, stripped of surrounding whitespace.
        """
        for name in names:
            value = os.environ.get(name, "").strip()
            if value and not value.startswith("${"):
                return value
        return default

    @classmethod
    def client(cls) -> str:
        """Returns the name of the client writing to the graph, recorded as provenance.

        Client configs set ``CONTEXT_GRAPH_CLIENT`` (``claude-code``, ``cursor``); anything else,
        such as the CLI, is ``cli``.
        """
        return cls.get("CONTEXT_GRAPH_CLIENT", default="cli")

    @classmethod
    def project_dir(cls) -> str:
        """Returns the directory of the project the session works on.

        The client config sets ``CONTEXT_GRAPH_PROJECT_DIR`` (e.g. Cursor from ``${workspaceFolder}``)
        and Claude Code sets ``CLAUDE_PROJECT_DIR``; otherwise the working directory is used.
        """
        return cls.get("CONTEXT_GRAPH_PROJECT_DIR", "CLAUDE_PROJECT_DIR", default=os.getcwd())


class LocalCredentials:
    """The generated password of the bundled local Neo4j, stored outside any project.

    Attributes:
        home: Folder holding the password file, ``~/.context-graph`` unless
            ``CONTEXT_GRAPH_HOME`` says otherwise.
    """

    FILE_NAME = "neo4j-password"

    def __init__(self, home: Path | None = None):
        self.home = home or Path(
            Environment.get("CONTEXT_GRAPH_HOME", default=str(Path.home() / ".context-graph"))
        ).expanduser()

    @property
    def password_file(self) -> Path:
        """Path of the password file."""
        return self.home / self.FILE_NAME

    def read(self) -> str:
        """Returns the stored password, or an empty string if there is none yet."""
        if not self.password_file.exists():
            return ""
        return self.password_file.read_text(encoding="utf-8").strip()

    def ensure(self) -> str:
        """Returns the stored password, generating and saving a random one first if needed."""
        existing = self.read()
        if existing:
            return existing
        self.home.mkdir(parents=True, exist_ok=True)
        password = secrets.token_urlsafe(24)
        self.password_file.write_text(password + "\n", encoding="utf-8")
        try:
            self.password_file.chmod(0o600)
        except OSError:
            pass
        return password


@dataclass
class Settings:
    """Neo4j connection settings.

    Attributes:
        uri: Bolt URI.
        user: Database user.
        password: Database password; empty when none is configured yet.
        database: Database name.
    """

    DEFAULT_URI = "bolt://127.0.0.1:7688"

    uri: str
    user: str
    password: str
    database: str

    @classmethod
    def from_environment(cls) -> "Settings":
        """Resolves the settings from the environment and the local password file.

        Each value comes from ``CONTEXT_GRAPH_NEO4J_*`` (set by client configs), then
        ``CLAUDE_PLUGIN_OPTION_NEO4J_*`` (exported to Claude Code plugin hooks), then the defaults
        of the bundled local container. The password falls back to the local password file.

        Returns:
            The resolved settings.
        """
        get = Environment.get
        return cls(
            uri=get("CONTEXT_GRAPH_NEO4J_URI", "CLAUDE_PLUGIN_OPTION_NEO4J_URI", default=cls.DEFAULT_URI),
            user=get("CONTEXT_GRAPH_NEO4J_USER", "CLAUDE_PLUGIN_OPTION_NEO4J_USER", default="neo4j"),
            password=get("CONTEXT_GRAPH_NEO4J_PASSWORD", "CLAUDE_PLUGIN_OPTION_NEO4J_PASSWORD")
            or LocalCredentials().read(),
            database=get("CONTEXT_GRAPH_NEO4J_DATABASE", "CLAUDE_PLUGIN_OPTION_NEO4J_DATABASE",
                         default="neo4j"),
        )

    @property
    def is_local(self) -> bool:
        """Whether the URI points at this machine (the bundled Docker database)."""
        return "127.0.0.1" in self.uri or "localhost" in self.uri
