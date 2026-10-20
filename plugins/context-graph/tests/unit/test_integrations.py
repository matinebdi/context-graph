import json
from pathlib import Path

import pytest

from context_graph.cli import SessionStartHook
from context_graph.domain import GraphError
from context_graph.integrations import ClientIntegration, CursorIntegration, PythonRuntime

PLUGIN_ROOT = Path(__file__).resolve().parents[2]


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def fake_python(monkeypatch):
    monkeypatch.setattr(PythonRuntime, "interpreter", classmethod(lambda cls: "C:\\Program Files\\cg\\python.exe"))


class TestCursorIntegration:
    def test_install_merges_and_is_idempotent(self, tmp_path, fake_python):
        cursor = tmp_path / ".cursor"
        cursor.mkdir()
        (cursor / "mcp.json").write_text('{"mcpServers": {"other": {"command": "x"}}}')
        (cursor / "hooks.json").write_text('{"version": 1, "hooks": {"sessionStart": [{"command": "echo hi"}]}}')

        CursorIntegration(tmp_path).install()
        CursorIntegration(tmp_path).install()

        servers = read(cursor / "mcp.json")["mcpServers"]
        assert set(servers) == {"other", "context-graph"}
        assert servers["context-graph"]["args"] == ["-m", "context_graph.server"]
        assert servers["context-graph"]["env"] == {"CONTEXT_GRAPH_PROJECT_DIR": "${workspaceFolder}",
                                                   "CONTEXT_GRAPH_CLIENT": "cursor"}
        starts = read(cursor / "hooks.json")["hooks"]["sessionStart"]
        assert starts[0] == {"command": "echo hi"} and len(starts) == 2
        # forward slashes and quotes (the path has a space) so any shell can run it
        assert starts[1]["command"] == '"C:/Program Files/cg/python.exe" -m context_graph.cli hook session-start --format cursor'
        stops = read(cursor / "hooks.json")["hooks"]["stop"]
        assert stops == [{"command": '"C:/Program Files/cg/python.exe" -m context_graph.cli hook stop --format cursor'}]
        rule = (cursor / "rules" / "context-graph.mdc").read_text(encoding="utf-8")
        assert rule.startswith("---\n") and "alwaysApply: true" in rule and "search_graph" in rule

    def test_uninstall_removes_only_ours(self, tmp_path, fake_python):
        cursor = tmp_path / ".cursor"
        cursor.mkdir()
        (cursor / "hooks.json").write_text('{"version": 1, "hooks": {"sessionStart": [{"command": "echo hi"}]}}')
        integration = CursorIntegration(tmp_path)
        integration.install()
        assert len(integration.uninstall()) == 3
        assert read(cursor / "mcp.json")["mcpServers"] == {}
        assert read(cursor / "hooks.json")["hooks"] == {"sessionStart": [{"command": "echo hi"}]}
        assert not (cursor / "rules" / "context-graph.mdc").exists()
        assert integration.uninstall() == []

    def test_refuses_to_clobber_broken_json(self, tmp_path, fake_python):
        (tmp_path / ".cursor").mkdir()
        (tmp_path / ".cursor" / "mcp.json").write_text("{not json")
        with pytest.raises(GraphError, match="not valid JSON"):
            CursorIntegration(tmp_path).install()
        assert (tmp_path / ".cursor" / "mcp.json").read_text() == "{not json"


class TestPythonRuntime:
    def test_refuses_ephemeral_uvx_environment(self, monkeypatch):
        monkeypatch.setattr("sys.executable", "/home/u/.cache/uv/archive-v0/abc/bin/python")
        with pytest.raises(GraphError, match="uv tool install"):
            PythonRuntime.interpreter()


class TestAgentInstructions:
    def test_claude_skill_matches_shared_instructions(self):
        skill = (PLUGIN_ROOT / "skills" / "context-graph" / "SKILL.md").read_text(encoding="utf-8")
        assert skill.endswith(ClientIntegration.agent_instructions()), \
            "skills/context-graph/SKILL.md is out of sync with assets/agent-instructions.md"


class TestSessionStartHook:
    def test_directory_precedence(self, monkeypatch):
        monkeypatch.delenv("CONTEXT_GRAPH_PROJECT_DIR", raising=False)
        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
        assert SessionStartHook.directory({"workspace_roots": ["/w"], "cwd": "/c"}) == "/w"
        assert SessionStartHook.directory({"cwd": "/c"}) == "/c"
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/claude")
        assert SessionStartHook.directory({"workspace_roots": ["/w"]}) == "/claude"
        monkeypatch.setenv("CONTEXT_GRAPH_PROJECT_DIR", "/explicit")
        assert SessionStartHook.directory({"workspace_roots": ["/w"]}) == "/explicit"
