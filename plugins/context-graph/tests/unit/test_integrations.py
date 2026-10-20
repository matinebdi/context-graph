import json
from pathlib import Path

import pytest

from context_graph.cli import SessionStartHook
from context_graph.domain import GraphError
from context_graph.config import Environment
from context_graph.integrations import AgentFiles, ClientIntegration, CursorIntegration, PythonRuntime

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


class TestAgentFiles:
    def test_generated_files_match_shared_instructions(self):
        assert AgentFiles(PLUGIN_ROOT).stale() == [], \
            "run `context-graph generate-agent-files`: the Claude skill or Cursor rule is out of sync"

    def test_write_regenerates_stale_files(self, tmp_path):
        files = AgentFiles(tmp_path)
        assert set(files.write()) == {AgentFiles.CLAUDE_SKILL, AgentFiles.CURSOR_RULE}
        assert files.stale() == [] and files.write() == []
        rule = (tmp_path / AgentFiles.CURSOR_RULE).read_text(encoding="utf-8")
        assert "alwaysApply: true" in rule and rule.endswith(ClientIntegration.agent_instructions())


class TestCursorPlugin:
    def test_manifest_paths_exist_and_avoid_claude_files(self):
        manifest = read(PLUGIN_ROOT / ".cursor-plugin" / "plugin.json")
        for field in ("rules", "commands", "skills", "hooks", "mcpServers", "logo"):
            assert (PLUGIN_ROOT / manifest[field]).exists(), field
        assert manifest["mcpServers"] == "mcp.json" and manifest["skills"].startswith("cursor/")
        claude = read(PLUGIN_ROOT / ".claude-plugin" / "plugin.json")
        assert manifest["version"] == claude["version"]

    def test_mcp_and_hooks_use_the_plugin_root(self):
        server = read(PLUGIN_ROOT / "mcp.json")["mcpServers"]["context-graph"]
        assert "${CURSOR_PLUGIN_ROOT}" in server["args"] and server["env"]["CONTEXT_GRAPH_CLIENT"] == "cursor"
        hooks = read(PLUGIN_ROOT / "cursor" / "hooks.json")["hooks"]
        assert {e["command"] for event in hooks.values() for e in event} == {
            "./cursor/hook.sh session-start", "./cursor/hook.sh stop"}


class TestSessionStartHook:
    def test_directory_precedence(self, monkeypatch):
        for name in Environment.PROJECT_DIR_VARIABLES:
            monkeypatch.delenv(name, raising=False)
        assert SessionStartHook.directory({"workspace_roots": ["/w"], "cwd": "/c"}) == "/w"
        assert SessionStartHook.directory({"cwd": "/c"}) == "/c"
        monkeypatch.setenv("CURSOR_PROJECT_DIR", "/cursor")
        assert SessionStartHook.directory({"workspace_roots": ["/w"]}) == "/cursor"
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/claude")
        assert SessionStartHook.directory({"workspace_roots": ["/w"]}) == "/claude"
        monkeypatch.setenv("CONTEXT_GRAPH_PROJECT_DIR", "/explicit")
        assert SessionStartHook.directory({"workspace_roots": ["/w"]}) == "/explicit"


class TestProjectDirGuard:
    def test_plugin_install_folder_never_counts_as_a_project(self, monkeypatch):
        for name in Environment.PROJECT_DIR_VARIABLES:
            monkeypatch.delenv(name, raising=False)
        monkeypatch.chdir(PLUGIN_ROOT)
        assert Environment.project_dir() == ""
        assert SessionStartHook.directory({"cwd": str(PLUGIN_ROOT / "src")}) == ""
        monkeypatch.setenv("CONTEXT_GRAPH_PROJECT_DIR", "${workspaceFolder}")  # not expanded by the client
        assert Environment.project_dir() == ""
        monkeypatch.setenv("CURSOR_PROJECT_DIR", str(PLUGIN_ROOT.parent))
        assert Environment.project_dir() == str(PLUGIN_ROOT.parent)
