"""Command line for humans, skills and client hooks.

    context-graph install cursor|claude [--dir D]   # wire the graph into an AI client for a project
    context-graph uninstall cursor [--dir D]
    context-graph up | down            # start/stop the bundled local Neo4j (Docker) and init schema
    context-graph status
    context-graph hook session-start [--format claude|cursor]   # session-start hook: hook JSON on stdin
    context-graph overview [--all]
    context-graph show <id> [--depth 2]
    context-graph search <query> [--type T]
    context-graph export <file.json> | import <file.json> | import-markdown <dir>
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import install
from .config import Settings, ensure_local_password, home_dir, project_dir
from .model import GraphError, Node
from .neo4j_store import Neo4jStore
from .render import brief, render_node, render_overview

COMPOSE_FILE = Path(__file__).resolve().parent / "assets" / "compose.yml"

HOOK_FOOTER = """\
---
This is your persistent context graph (MCP server `context-graph`, Neo4j). Use it as long-term
memory: search_graph / get_node before asking the user things they may have told you already, and
record durable facts, preferences, decisions (with the why), components, resources and open tasks
with upsert_node as you learn them. Call log_session after meaningful work."""

OFFLINE = "[context-graph] The context graph is offline ({reason}). {hint}"
# How to start the database / configure an external one, per client.
START = {"claude": "/context-graph:setup", "cursor": "`context-graph up` in a terminal"}
EXTERNAL = {
    "claude": "the plugin's neo4j_* options in /config",
    "cursor": "CONTEXT_GRAPH_NEO4J_URI / _USER / _PASSWORD in the env of .cursor/mcp.json",
}


def offline_hint(uri: str, client: str = "claude") -> str:
    local = "127.0.0.1" in uri or "localhost" in uri
    if local and shutil.which("docker") is None:
        return (
            "Docker is not installed (or not on PATH), and the local database needs it. Tell the "
            f"user to either install Docker Desktop and run {START[client]}, or point the context "
            f"graph at an external Neo4j (for example a free Neo4j Aura instance) with {EXTERNAL[client]}."
        )
    return f"Tell the user they can run {START[client]} to start it."


def hook_directory(payload: dict) -> str:
    roots = payload.get("workspace_roots") or []
    return (os.environ.get("CONTEXT_GRAPH_PROJECT_DIR") or os.environ.get("CLAUDE_PROJECT_DIR")
            or (roots[0] if roots else None) or payload.get("cwd") or os.getcwd())


def hook_session_start(store: Neo4jStore, client: str = "claude") -> str:
    """Context for the start of a session: plain text for Claude Code, JSON for Cursor."""
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        payload = {}
    text = session_context(store, hook_directory(payload), client)
    return json.dumps({"additional_context": text}) if client == "cursor" else text


def session_context(store: Neo4jStore, directory: str, client: str) -> str:
    try:
        store.ping()
        project = store.ensure_project(directory)
        parts = [
            render_overview(store, project.id),
            f"# Current project: `{project.id}` ({directory})\n\n" + render_node(store, project.id),
            HOOK_FOOTER,
        ]
        return "\n\n".join(parts)
    except GraphError as e:
        return OFFLINE.format(reason=str(e).split(". ")[0], hint=offline_hint(store.settings.uri, client))


def compose(*args: str, password: str) -> int:
    env = {**os.environ, "CONTEXT_GRAPH_NEO4J_PASSWORD": password}
    return subprocess.run(["docker", "compose", "-f", str(COMPOSE_FILE), *args], env=env).returncode


def cmd_up(timeout: int = 180) -> int:
    password = ensure_local_password()
    if compose("up", "-d", password=password) != 0:
        print("docker compose failed. Is Docker installed and running?", file=sys.stderr)
        return 1
    store = Neo4jStore(Settings.from_env())
    deadline = time.time() + timeout
    print("Waiting for Neo4j to accept connections", end="", flush=True)
    while True:
        try:
            store.ping()
            break
        except GraphError as e:
            if "credentials" in str(e):
                print(f"\n{e} The data volume was probably created with another password; "
                      f"restore it in {home_dir() / 'neo4j-password'} or remove the volume "
                      "`context-graph_neo4j-data`.", file=sys.stderr)
                return 1
            if time.time() > deadline:
                print(f"\nTimed out: {e}", file=sys.stderr)
                return 1
            print(".", end="", flush=True)
            time.sleep(3)
    store.init_schema()
    print(f"\nNeo4j is up at {store.settings.uri}; schema ready.")
    print(f"Browser: http://127.0.0.1:{os.environ.get('CONTEXT_GRAPH_HTTP_PORT', '7475')}  (user neo4j, password in "
          f"{home_dir() / 'neo4j-password'})")
    return 0


def cmd_status(store: Neo4jStore, directory: str) -> int:
    s = store.settings
    print(f"Neo4j: {s.uri}  database={s.database}  user={s.user}")
    try:
        store.ping()
    except GraphError as e:
        print(f"Status: OFFLINE — {e}")
        return 1
    counts = store.counts()
    print(f"Status: online, {sum(counts.values())} nodes")
    for t, c in counts.items():
        print(f"  {t}: {c}")
    projects = store.projects_for_path(directory)
    print(f"Project for {directory}: " + (projects[0].id if projects else "none registered yet"))
    return 0


def cmd_install(action: str, client: str, project: Path) -> int:
    if client == "claude":
        print(install.CLAUDE_STEPS if action == "install" else
              "claude plugin uninstall context-graph@context-graph  (run inside the project)")
        return 0
    do_install, do_uninstall = install.CLIENTS[client]
    try:
        changed = (do_install if action == "install" else do_uninstall)(project)
    except GraphError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    if action == "uninstall":
        print("Removed from: " + (", ".join(changed) or "nothing to remove"))
        return 0
    print(f"Wired the context graph into {client} for {project.resolve()}:")
    for f in changed:
        print(f"  {f}")
    print(
        "\nNext:\n"
        "  1. context-graph up        (once per machine: starts the local Neo4j in Docker)\n"
        "  2. Restart Cursor, then enable the `context-graph` server under Settings -> MCP if asked.\n"
        "  3. The paths written in .cursor/ are specific to this machine: each teammate runs\n"
        "     `context-graph install cursor` themselves instead of sharing those files."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="context-graph")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("up", help="start the local Neo4j container and initialise the schema")
    sub.add_parser("down", help="stop the local Neo4j container (data is kept)")
    p_status = sub.add_parser("status")
    p_status.add_argument("--dir", default=None)
    p_hook = sub.add_parser("hook")
    p_hook.add_argument("event", choices=["session-start"])
    p_hook.add_argument("--format", choices=["claude", "cursor"], default="claude")
    for name, text in (("install", "wire the context graph into an AI client for a project"),
                       ("uninstall", "remove what `install` added")):
        p = sub.add_parser(name, help=text)
        p.add_argument("client", choices=sorted([*install.CLIENTS, "claude"]))
        p.add_argument("--dir", default=".", help="project directory (default: current)")
    sub.add_parser("init-schema")
    p_ov = sub.add_parser("overview")
    p_ov.add_argument("--all", action="store_true", help="ignore the current project scope")
    p_show = sub.add_parser("show")
    p_show.add_argument("id")
    p_show.add_argument("--depth", type=int, default=1)
    p_search = sub.add_parser("search")
    p_search.add_argument("query")
    p_search.add_argument("--type")
    p_export = sub.add_parser("export")
    p_export.add_argument("file")
    p_import = sub.add_parser("import")
    p_import.add_argument("file")
    p_md = sub.add_parser("import-markdown", help="import the legacy one-markdown-file-per-node format")
    p_md.add_argument("dir")
    args = parser.parse_args(argv)

    # Output may go to Claude's context; make sure unicode survives Windows consoles.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

    if args.cmd in ("install", "uninstall"):
        return cmd_install(args.cmd, args.client, Path(args.dir))

    store = Neo4jStore()
    try:
        if args.cmd == "hook":
            print(hook_session_start(store, args.format))
            return 0  # a hook must never block the session
        if args.cmd == "up":
            return cmd_up()
        if args.cmd == "down":
            return compose("stop", password=ensure_local_password())
        if args.cmd == "status":
            return cmd_status(store, args.dir or project_dir())
        if args.cmd == "init-schema":
            store.init_schema()
            print("Schema ready.")
        elif args.cmd == "overview":
            project = None if args.all else store.ensure_project(project_dir()).id
            print(render_overview(store, project))
        elif args.cmd == "show":
            print(render_node(store, args.id, depth=args.depth))
        elif args.cmd == "search":
            hits = store.search(args.query, type=args.type)
            print("\n".join(brief(n) for _, n in hits) or "No matches.")
        elif args.cmd == "export":
            data = {"format": "context-graph", "version": 1, "nodes": store.export_all()}
            Path(args.file).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            print(f"Exported {len(data['nodes'])} nodes to {args.file}")
        elif args.cmd == "import":
            data = json.loads(Path(args.file).read_text(encoding="utf-8"))
            n = store.import_nodes([Node.from_dict(d) for d in data["nodes"]])
            print(f"Imported {n} nodes.")
        elif args.cmd == "import-markdown":
            files = sorted(Path(args.dir).glob("*.md"))
            nodes = [Node.from_markdown(f.read_text(encoding="utf-8")) for f in files]
            n = store.import_nodes(nodes)
            print(f"Imported {n} nodes from {args.dir}.")
        return 0
    except GraphError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    finally:
        store.close()


if __name__ == "__main__":
    sys.exit(main())
