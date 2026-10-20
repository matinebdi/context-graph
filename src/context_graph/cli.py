from __future__ import annotations

import argparse
import json
import os
import sys

from .render import line, render_node, render_overview, render_project_context
from .store import GraphStore

HOOK_FOOTER = """\
---
This is your persistent context graph (MCP server `context-graph`). Use it as long-term memory:
search/get_node before asking things the user may have told you already, and record new
durable facts, preferences, decisions (with the why), projects and open tasks as you learn them."""


def session_start(store: GraphStore) -> str:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        payload = {}
    cwd = payload.get("cwd") or os.getcwd()
    parts = [render_overview(store)]
    project = render_project_context(store, cwd)
    if project:
        parts.append(f"# Current project (cwd: {cwd})\n\n{project}")
    else:
        parts.append(f"No project node is registered for cwd {cwd}.")
    parts.append(HOOK_FOOTER)
    return "\n\n".join(parts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="context-graph")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("session-start")
    sub.add_parser("overview")
    p_show = sub.add_parser("show")
    p_show.add_argument("id")
    p_show.add_argument("--depth", type=int, default=1)
    p_search = sub.add_parser("search")
    p_search.add_argument("query")
    p_search.add_argument("--type")
    sub.add_parser("check")
    args = parser.parse_args(argv)

    # Hook output goes to Claude's context; make sure unicode survives Windows consoles.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    store = GraphStore()
    if args.cmd == "session-start":
        print(session_start(store))
    elif args.cmd == "overview":
        print(render_overview(store))
    elif args.cmd == "show":
        print(render_node(store, args.id, depth=args.depth))
    elif args.cmd == "search":
        hits = store.search(args.query, type=args.type)
        print("\n".join(line(n) for _, n in hits) or "No matches.")
    elif args.cmd == "check":
        bad = store.dangling()
        for src, rel, tgt in bad:
            print(f"dangling: {src} -{rel}-> {tgt}")
        print("OK" if not bad else f"{len(bad)} dangling relation(s)")
        return 1 if bad else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
