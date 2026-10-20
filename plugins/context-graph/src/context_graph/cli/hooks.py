import hashlib
import json
import os
import shutil
import time
from pathlib import Path

from context_graph.config import LocalCredentials
from context_graph.domain import GraphError
from context_graph.rendering import MarkdownRenderer
from context_graph.storage import Neo4jStore


class TranscriptActivity:
    """The tool calls of a session, read from the client's JSONL transcript.

    Claude Code writes assistant turns as ``{"type": "assistant", "message": {"content": [...]}}``
    with ``{"type": "tool_use", "name": ...}`` items. Other formats are read best-effort from
    assistant entries (``tool_use`` items or OpenAI-style ``tool_calls``); an unreadable transcript
    yields no calls, which never triggers a nudge.

    Attributes:
        path: Transcript file.
    """

    # Tools that put knowledge into the graph; any of them counts as "recorded".
    WRITE_TOOLS = ("upsert_node", "link", "log_session", "merge_nodes", "set_node_project")

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def tool_calls(self) -> list[str]:
        """Returns the names of the tools called in the session, in order."""
        names, seen = [], set()
        try:
            lines = self.path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return []
        for line in lines:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(entry, dict):
                continue
            for call_id, name in self._calls_in(entry):
                if call_id is None or call_id not in seen:
                    seen.add(call_id)
                    names.append(name)
        return names

    def progress(self) -> tuple[int, int]:
        """Returns ``(position of the last graph write, tool calls since then)``.

        The position is the number of tool calls up to and including the last write to the
        graph; 0 when there was none.
        """
        calls = self.tool_calls()
        last_write = 0
        for index, name in enumerate(calls, start=1):
            if name.endswith(self.WRITE_TOOLS):
                last_write = index
        return last_write, len(calls) - last_write

    @staticmethod
    def _calls_in(entry: dict):
        if entry.get("type") == "assistant" and isinstance(entry.get("message"), dict):
            content = entry["message"].get("content")
        elif entry.get("role") == "assistant":
            content = entry.get("content")
            for call in entry.get("tool_calls") or []:
                function = (call.get("function") or {}) if isinstance(call, dict) else {}
                if function.get("name"):
                    yield call.get("id"), function["name"]
        else:
            return
        for item in content if isinstance(content, list) else []:
            if isinstance(item, dict) and item.get("type") in ("tool_use", "tool_call") and item.get("name"):
                yield item.get("id"), item["name"]


class StopHook:
    """Nudges the agent, once, to record what it learned after substantial unrecorded work.

    It fires when at least ``THRESHOLD`` tool calls happened since the last graph write, and
    then stays quiet until the agent writes to the graph again. It never fires while the client
    is already continuing because of a stop hook.

    Attributes:
        client: ``claude`` or ``cursor``; decides the output format.
        state_dir: Folder remembering, per session, where the last nudge happened.
    """

    THRESHOLD = 15
    STATE_MAX_AGE_DAYS = 14
    MESSAGE = (
        "[context-graph] This session did substantial work that isn't recorded in the context graph yet. "
        "Before finishing, record what is worth keeping for future sessions: decisions (with the why), "
        "how components work, user preferences or corrections, and open tasks, using upsert_node "
        "(search first and update existing nodes). Then call log_session with a one-line summary. "
        "If nothing is worth keeping, say so in one sentence and stop."
    )

    def __init__(self, client: str = "claude", state_dir: Path | None = None):
        self.client = client
        self.state_dir = state_dir or LocalCredentials().home / "sessions"

    def respond(self, stdin_text: str) -> str:
        """Returns the hook output for the hook's stdin payload; empty output lets the agent stop.

        Args:
            stdin_text: JSON the client passes on stdin.
        """
        try:
            payload = json.loads(stdin_text or "{}")
        except json.JSONDecodeError:
            payload = {}
        message = self.nudge(payload)
        if self.client == "cursor":
            return json.dumps({"followup_message": message} if message else {})
        if not message:
            return ""
        return json.dumps({"hookSpecificOutput": {"hookEventName": "Stop", "additionalContext": message}})

    def nudge(self, payload: dict) -> str | None:
        """Decides whether to nudge.

        Args:
            payload: Hook stdin JSON (``transcript_path``, ``session_id`` or ``conversation_id``,
                ``stop_hook_active`` for Claude Code, ``status`` and ``loop_count`` for Cursor).

        Returns:
            The nudge message, or None.
        """
        if payload.get("stop_hook_active") or payload.get("loop_count") or payload.get("status") not in (None, "completed"):
            return None
        transcript = payload.get("transcript_path")
        session = payload.get("session_id") or payload.get("conversation_id")
        if not transcript or not session:
            return None
        last_write, since = TranscriptActivity(transcript).progress()
        state_file = self.state_dir / (hashlib.sha1(str(session).encode()).hexdigest()[:16] + ".json")
        if since < self.THRESHOLD or self._nudged_after(state_file) == last_write:
            return None
        self._remember(state_file, last_write)
        return self.MESSAGE

    @staticmethod
    def _nudged_after(state_file: Path) -> int | None:
        try:
            return json.loads(state_file.read_text(encoding="utf-8"))["nudged_after"]
        except (OSError, ValueError, KeyError):
            return None

    def _remember(self, state_file: Path, last_write: int) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps({"nudged_after": last_write}), encoding="utf-8")
        cutoff = time.time() - self.STATE_MAX_AGE_DAYS * 86400
        for old in self.state_dir.glob("*.json"):
            try:
                if old.stat().st_mtime < cutoff:
                    old.unlink()
            except OSError:
                pass


class SessionStartHook:
    """Builds the context injected when a client session starts.

    Never fails: when the graph is unreachable it returns a one-line notice that tells the agent
    what the user can do about it.

    Attributes:
        store: Graph store.
        renderer: Markdown views over the store.
        client: ``claude`` (plain-text output) or ``cursor`` (JSON output).
    """

    CLIENTS = ("claude", "cursor")
    FOOTER = (
        "---\n"
        "This is your persistent context graph (MCP server `context-graph`, Neo4j). Use it as long-term\n"
        "memory: search_graph / get_node before asking the user things they may have told you already, and\n"
        "record durable facts, preferences, decisions (with the why), components, resources and open tasks\n"
        "with upsert_node as you learn them. Call log_session after meaningful work."
    )
    START = {"claude": "/context-graph:setup", "cursor": "`context-graph up` in a terminal"}
    EXTERNAL = {
        "claude": "the plugin's neo4j_* options in /config",
        "cursor": "CONTEXT_GRAPH_NEO4J_URI / _USER / _PASSWORD in the env of .cursor/mcp.json",
    }

    def __init__(self, store: Neo4jStore, client: str = "claude"):
        self.store = store
        self.renderer = MarkdownRenderer(store)
        self.client = client

    def respond(self, stdin_text: str) -> str:
        """Returns the hook output for the hook's stdin payload, in the client's format.

        Args:
            stdin_text: JSON the client passes on stdin; may be empty.
        """
        try:
            payload = json.loads(stdin_text or "{}")
        except json.JSONDecodeError:
            payload = {}
        context = self.context(self.directory(payload))
        return json.dumps({"additional_context": context}) if self.client == "cursor" else context

    def context(self, directory: str) -> str:
        """Returns the overview and project page for ``directory``, or an offline notice."""
        try:
            self.store.ping()
            project = self.store.ensure_project(directory)
            return "\n\n".join([
                self.renderer.overview(project.id),
                f"# Current project: `{project.id}` ({directory})\n\n" + self.renderer.node(project.id),
                self.FOOTER,
            ])
        except GraphError as exc:
            reason = str(exc).split(". ")[0]
            return f"[context-graph] The context graph is offline ({reason}). {self.offline_hint()}"

    def offline_hint(self) -> str:
        """Returns what the user can do when the graph is unreachable, for this client."""
        if self.store.settings.is_local and shutil.which("docker") is None:
            return (
                "Docker is not installed (or not on PATH), and the local database needs it. Tell the "
                f"user to either install Docker Desktop and run {self.START[self.client]}, or point the "
                "context graph at an external Neo4j (for example a free Neo4j Aura instance) with "
                f"{self.EXTERNAL[self.client]}."
            )
        return f"Tell the user they can run {self.START[self.client]} to start it."

    @staticmethod
    def directory(payload: dict) -> str:
        """Resolves the project directory of the session.

        Order: ``CONTEXT_GRAPH_PROJECT_DIR``, ``CLAUDE_PROJECT_DIR``, the payload's first
        ``workspace_roots`` entry (Cursor), its ``cwd`` (Claude Code), the working directory.

        Args:
            payload: Hook stdin JSON.
        """
        roots = payload.get("workspace_roots") or []
        return (
            os.environ.get("CONTEXT_GRAPH_PROJECT_DIR")
            or os.environ.get("CLAUDE_PROJECT_DIR")
            or (roots[0] if roots else None)
            or payload.get("cwd")
            or os.getcwd()
        )
