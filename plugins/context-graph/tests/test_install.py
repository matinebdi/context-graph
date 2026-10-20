import json
from pathlib import Path

import pytest

from context_graph import install
from context_graph.cli import hook_directory
from context_graph.model import GraphError

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def fake_exes(monkeypatch):
    monkeypatch.setattr(install, "interpreter", lambda: "C:\\Program Files\\cg\\python.exe")


def read(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def test_cursor_install_merges_and_is_idempotent(tmp_path, fake_exes):
    cursor = tmp_path / ".cursor"
    cursor.mkdir()
    (cursor / "mcp.json").write_text('{"mcpServers": {"other": {"command": "x"}}}')
    (cursor / "hooks.json").write_text('{"version": 1, "hooks": {"sessionStart": [{"command": "echo hi"}]}}')

    install.install_cursor(tmp_path)
    install.install_cursor(tmp_path)

    mcp = read(cursor / "mcp.json")["mcpServers"]
    assert set(mcp) == {"other", "context-graph"}
    assert mcp["context-graph"]["env"] == {"CONTEXT_GRAPH_PROJECT_DIR": "${workspaceFolder}"}
    assert mcp["context-graph"]["args"] == ["-m", "context_graph.server"]
    starts = read(cursor / "hooks.json")["hooks"]["sessionStart"]
    assert starts[0] == {"command": "echo hi"} and len(starts) == 2
    # forward slashes and quotes (path has a space) so any shell can run it
    assert starts[1]["command"] == '"C:/Program Files/cg/python.exe" -m context_graph.cli hook session-start --format cursor'
    rule = (cursor / "rules" / "context-graph.mdc").read_text(encoding="utf-8")
    assert rule.startswith("---\n") and "alwaysApply: true" in rule and "search_graph" in rule


def test_cursor_uninstall_removes_only_ours(tmp_path, fake_exes):
    cursor = tmp_path / ".cursor"
    cursor.mkdir()
    (cursor / "hooks.json").write_text('{"version": 1, "hooks": {"sessionStart": [{"command": "echo hi"}]}}')
    install.install_cursor(tmp_path)
    changed = install.uninstall_cursor(tmp_path)
    assert len(changed) == 3
    assert read(cursor / "mcp.json")["mcpServers"] == {}
    assert read(cursor / "hooks.json")["hooks"]["sessionStart"] == [{"command": "echo hi"}]
    assert not (cursor / "rules" / "context-graph.mdc").exists()
    assert install.uninstall_cursor(tmp_path) == []


def test_cursor_install_refuses_to_clobber_broken_json(tmp_path, fake_exes):
    (tmp_path / ".cursor").mkdir()
    (tmp_path / ".cursor" / "mcp.json").write_text("{not json")
    with pytest.raises(GraphError, match="not valid JSON"):
        install.install_cursor(tmp_path)
    assert (tmp_path / ".cursor" / "mcp.json").read_text() == "{not json"


def test_claude_skill_matches_shared_instructions():
    skill = (ROOT / "skills" / "context-graph" / "SKILL.md").read_text(encoding="utf-8")
    assert skill.endswith(install.agent_instructions()), \
        "skills/context-graph/SKILL.md is out of sync with assets/agent-instructions.md"


def test_hook_directory_precedence(monkeypatch):
    monkeypatch.delenv("CONTEXT_GRAPH_PROJECT_DIR", raising=False)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    assert hook_directory({"workspace_roots": ["/w"], "cwd": "/c"}) == "/w"
    assert hook_directory({"cwd": "/c"}) == "/c"
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/claude")
    assert hook_directory({"workspace_roots": ["/w"]}) == "/claude"
    monkeypatch.setenv("CONTEXT_GRAPH_PROJECT_DIR", "/explicit")
    assert hook_directory({"workspace_roots": ["/w"]}) == "/explicit"


def test_installer_refuses_ephemeral_uvx_env(monkeypatch):
    monkeypatch.setattr(install.sys, "executable", "/home/u/.cache/uv/archive-v0/abc/bin/python")
    with pytest.raises(GraphError, match="uv tool install"):
        install.interpreter()
