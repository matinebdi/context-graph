

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path


def _env(*names: str, default: str = "") -> str:
    for name in names:
        value = os.environ.get(name, "").strip()
        # An unset userConfig option can reach us as a literal "${user_config.x}".
        if value and not value.startswith("${"):
            return value
    return default


def home_dir() -> Path:
    return Path(_env("CONTEXT_GRAPH_HOME", default=str(Path.home() / ".context-graph"))).expanduser()


def password_file() -> Path:
    return home_dir() / "neo4j-password"


def ensure_local_password() -> str:
    f = password_file()
    if f.exists():
        return f.read_text(encoding="utf-8").strip()
    f.parent.mkdir(parents=True, exist_ok=True)
    pw = secrets.token_urlsafe(24)
    f.write_text(pw + "\n", encoding="utf-8")
    try:
        f.chmod(0o600)
    except OSError:
        pass
    return pw


def project_dir() -> str:
    """The project the session works on: set explicitly by the client config
    (CONTEXT_GRAPH_PROJECT_DIR, e.g. Cursor's ${workspaceFolder}), by Claude Code
    (CLAUDE_PROJECT_DIR), or else the process working directory."""
    return _env("CONTEXT_GRAPH_PROJECT_DIR", "CLAUDE_PROJECT_DIR", default=os.getcwd())


@dataclass
class Settings:
    uri: str
    user: str
    password: str
    database: str

    @classmethod
    def from_env(cls) -> "Settings":
        password = _env("CONTEXT_GRAPH_NEO4J_PASSWORD", "CLAUDE_PLUGIN_OPTION_NEO4J_PASSWORD")
        if not password and password_file().exists():
            password = password_file().read_text(encoding="utf-8").strip()
        return cls(
            uri=_env("CONTEXT_GRAPH_NEO4J_URI", "CLAUDE_PLUGIN_OPTION_NEO4J_URI",
                     default="bolt://127.0.0.1:7688"),
            user=_env("CONTEXT_GRAPH_NEO4J_USER", "CLAUDE_PLUGIN_OPTION_NEO4J_USER", default="neo4j"),
            password=password,
            database=_env("CONTEXT_GRAPH_NEO4J_DATABASE", "CLAUDE_PLUGIN_OPTION_NEO4J_DATABASE",
                          default="neo4j"),
        )
