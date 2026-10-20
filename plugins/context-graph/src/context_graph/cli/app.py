import argparse
import json
import sys
from pathlib import Path

from context_graph.cli.database import LocalDatabase
from context_graph.cli.hooks import SessionStartHook, StopHook
from context_graph.config import Environment
from context_graph.domain import GraphError, Node
from context_graph.integrations import INTEGRATIONS, AgentFiles
from context_graph.rendering import MarkdownRenderer
from context_graph.storage import GraphMaintenance, Neo4jStore


class ContextGraphCLI:
    """The ``context-graph`` command line.

    Each subcommand ``name`` is handled by the method ``cmd_<name>`` (dashes become underscores),
    which returns a process exit code.

    Attributes:
        parser: Argument parser for every subcommand.
    """

    def __init__(self):
        self.parser = self._build_parser()
        self._store: Neo4jStore | None = None

    @property
    def store(self) -> Neo4jStore:
        """The graph store, created on first use so that offline commands never need it."""
        if self._store is None:
            self._store = Neo4jStore()
        return self._store

    def run(self, argv: list[str] | None = None) -> int:
        """Parses ``argv`` and runs the subcommand.

        Args:
            argv: Arguments without the program name; defaults to ``sys.argv[1:]``.

        Returns:
            The process exit code: 0 on success, 1 on a ``GraphError``.
        """
        args = self.parser.parse_args(argv)
        # Output may go into an agent's context; make sure unicode survives Windows consoles.
        for stream in (sys.stdout, sys.stderr):
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8")
        try:
            return getattr(self, "cmd_" + args.cmd.replace("-", "_"))(args)
        except GraphError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        finally:
            if self._store is not None:
                self._store.close()

    @classmethod
    def main(cls) -> int:
        """Console-script entry point."""
        return cls().run()

    # ------------------------------------------------------------------ commands

    def cmd_up(self, args) -> int:
        return LocalDatabase().up()

    def cmd_down(self, args) -> int:
        return LocalDatabase().down()

    def cmd_status(self, args) -> int:
        directory = args.dir or Environment.project_dir()
        settings = self.store.settings
        print(f"Neo4j: {settings.uri}  database={settings.database}  user={settings.user}")
        try:
            self.store.ping()
        except GraphError as exc:
            print(f"Status: OFFLINE — {exc}")
            return 1
        counts = self.store.counts()
        print(f"Status: online, {sum(counts.values())} nodes")
        for node_type, count in counts.items():
            print(f"  {node_type}: {count}")
        projects = self.store.projects_for_path(directory)
        print(f"Project for {directory}: " + (projects[0].id if projects else "none registered yet"))
        return 0

    def cmd_install(self, args) -> int:
        integration = INTEGRATIONS[args.client](Path(args.dir))
        written = integration.install()
        if written:
            print(f"Wired the context graph into {args.client} for {integration.project}:")
            for path in written:
                print(f"  {path.relative_to(integration.project)}")
        print("\nNext:\n" + integration.next_steps())
        return 0

    def cmd_uninstall(self, args) -> int:
        integration = INTEGRATIONS[args.client](Path(args.dir))
        changed = integration.uninstall()
        if args.client == "claude":
            print(integration.next_steps())
        else:
            print("Removed from: " + (", ".join(str(p.relative_to(integration.project)) for p in changed)
                                      or "nothing to remove"))
        return 0

    def cmd_hook(self, args) -> int:
        if args.event == "stop":
            output = StopHook(args.format).respond(sys.stdin.read())
        else:
            output = SessionStartHook(self.store, args.format).respond(sys.stdin.read())
        if output:
            print(output)
        return 0  # a hook must never block the session

    def cmd_generate_agent_files(self, args) -> int:
        written = AgentFiles().write()
        print("\n".join(f"wrote {path}" for path in written) or "agent files are up to date")
        return 0

    def cmd_review(self, args) -> int:
        print(GraphMaintenance(self.store).review(args.stale_days).to_markdown())
        return 0

    def cmd_reindex(self, args) -> int:
        print("Embedding nodes (the model downloads once on first use)…", flush=True)
        count = GraphMaintenance(self.store).reindex(force=args.all)
        print(f"Embedded {count} node(s).")
        return 0

    def cmd_init_schema(self, args) -> int:
        self.store.init_schema()
        print("Schema ready.")
        return 0

    def cmd_overview(self, args) -> int:
        project = None if args.all else self.store.ensure_project(Environment.project_dir()).id
        print(MarkdownRenderer(self.store).overview(project))
        return 0

    def cmd_show(self, args) -> int:
        print(MarkdownRenderer(self.store).node(args.id, depth=args.depth))
        return 0

    def cmd_search(self, args) -> int:
        hits = self.store.search(args.query, type=args.type)
        print("\n".join(MarkdownRenderer.brief(node) for _, node in hits) or "No matches.")
        return 0

    def cmd_export(self, args) -> int:
        data = {"format": "context-graph", "version": 1, "nodes": self.store.export_all()}
        Path(args.file).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Exported {len(data['nodes'])} nodes to {args.file}")
        return 0

    def cmd_import(self, args) -> int:
        data = json.loads(Path(args.file).read_text(encoding="utf-8"))
        count = self.store.import_nodes([Node.from_dict(d) for d in data["nodes"]])
        print(f"Imported {count} nodes.")
        return 0

    def cmd_import_markdown(self, args) -> int:
        files = sorted(Path(args.dir).glob("*.md"))
        count = self.store.import_nodes([Node.from_markdown(f.read_text(encoding="utf-8")) for f in files])
        print(f"Imported {count} nodes from {args.dir}.")
        return 0

    # ------------------------------------------------------------------ parser

    @staticmethod
    def _build_parser() -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(prog="context-graph", description="Persistent graph memory for AI agents.")
        sub = parser.add_subparsers(dest="cmd", required=True)
        sub.add_parser("up", help="start the local Neo4j container and create the schema")
        sub.add_parser("down", help="stop the local Neo4j container (data is kept)")
        status = sub.add_parser("status", help="connectivity, node counts and project mapping")
        status.add_argument("--dir", default=None, help="directory to map to a project")
        for name, text in (("install", "wire the context graph into an AI client for a project"),
                           ("uninstall", "remove what `install` added")):
            command = sub.add_parser(name, help=text)
            command.add_argument("client", choices=sorted(INTEGRATIONS))
            command.add_argument("--dir", default=".", help="project directory (default: current)")
        hook = sub.add_parser("hook", help="client hook entry point (reads the hook JSON on stdin)")
        hook.add_argument("event", choices=["session-start", "stop"])
        hook.add_argument("--format", choices=SessionStartHook.CLIENTS, default="claude")
        sub.add_parser("init-schema", help="create constraints and indexes (idempotent)")
        review = sub.add_parser("review", help="find duplicates, orphans, stale tasks, outdated dependencies")
        review.add_argument("--stale-days", type=int, default=30)
        sub.add_parser("generate-agent-files",
                       help="(development) regenerate the Claude skill and Cursor rule from agent-instructions.md")
        reindex = sub.add_parser("reindex", help="compute embeddings for semantic search")
        reindex.add_argument("--all", action="store_true", help="recompute every node, not only missing ones")
        overview = sub.add_parser("overview", help="the overview injected at session start")
        overview.add_argument("--all", action="store_true", help="ignore the current project scope")
        show = sub.add_parser("show", help="one node with its relations")
        show.add_argument("id")
        show.add_argument("--depth", type=int, default=1)
        search = sub.add_parser("search", help="full-text search")
        search.add_argument("query")
        search.add_argument("--type")
        export = sub.add_parser("export", help="dump the whole graph to JSON")
        export.add_argument("file")
        load = sub.add_parser("import", help="load a JSON export")
        load.add_argument("file")
        legacy = sub.add_parser("import-markdown", help="import the legacy one-markdown-file-per-node format")
        legacy.add_argument("dir")
        return parser
