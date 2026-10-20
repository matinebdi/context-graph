# Installation

[← README](../README.md)

- [Requirements](#requirements)
- [Claude Code](#claude-code)
- [Cursor](#cursor)
- [Start the database](#start-the-database)
- [External Neo4j (no Docker)](#external-neo4j-no-docker)
- [Verify](#verify)

## Requirements

| Component | Version | Notes |
|---|---|---|
| Claude Code **or** Cursor | Claude Code 2.x with plugins; Cursor with MCP and hooks | the client |
| [uv](https://docs.astral.sh/uv/) | ≥ 0.4 | creates the Python environment and runs the server and CLI. Downloads Python ≥ 3.11 if none is present |
| Docker Desktop / Docker Engine | Compose v2 (`docker compose`) | runs the bundled Neo4j. **Optional**: see [External Neo4j](#external-neo4j-no-docker) |
| Network | outbound HTTPS on first run | Python packages and the `neo4j:5.26-community` image (~600 MB) |
| RAM | ~1.5 GB free | Neo4j heap max 1 GB plus 512 MB page cache, tunable in `assets/compose.yml` |

Platforms: developed and verified on **Windows 11**, where Git Bash runs the hooks. macOS and
Linux are expected to work, since everything is POSIX shell plus Python, but they haven't been
verified yet.

For Claude Code, `uv` and `docker` must be on the `PATH` of the process that launches it. If you
start it from an IDE or a desktop shortcut, make sure that environment inherits your shell's
`PATH`. Cursor doesn't need this, because its config contains absolute paths.

## Claude Code

The repository is a plugin marketplace named `context-graph`. Add it once:

```sh
claude plugin marketplace add matinebdi/context-graph
```

Then enable the plugin in each project that should have graph memory:

```sh
cd /path/to/your/project
claude plugin install context-graph@context-graph --scope project
```

Inside Claude Code, one command does both steps:

```
/plugin install context-graph --marketplace matinebdi/context-graph
```

The `--scope` flag decides where the plugin is enabled:

| `--scope` | Written to | Effect |
|---|---|---|
| `project` | `<project>/.claude/settings.json` | this project, and collaborators if the file is committed |
| `local` | `<project>/.claude/settings.local.json` | this project, for you only |
| `user` | `~/.claude/settings.json` | every project |

When the plugin is enabled, Claude Code may ask for four options (`neo4j_uri`, `neo4j_user`,
`neo4j_password`, `neo4j_database`). **For the bundled local database, accept the defaults and
leave the password empty.** You can change them later with
`/plugin configure context-graph@context-graph` or in `/config`. See
[Reference → Configuration](reference.md#configuration).

**From a local clone** (for development):

```sh
git clone https://github.com/matinebdi/context-graph.git
claude plugin marketplace add ./context-graph
```

A directory marketplace is read in place, so your edits apply after `/reload-plugins` or a
restart.

## Cursor

Install the package once per machine as a uv tool. That puts the `context-graph` command on your
`PATH`:

```sh
uv tool install "git+https://github.com/matinebdi/context-graph#subdirectory=plugins/context-graph"
```

Then, in each project that should have graph memory:

```sh
cd /path/to/your/project
context-graph install cursor          # or: context-graph install cursor --dir /path/to/project
```

The installer merges three entries into the project's `.cursor/` folder and leaves existing
servers, hooks and rules untouched:

| File | Entry |
|---|---|
| `.cursor/mcp.json` | `mcpServers.context-graph`: runs `<python> -m context_graph.server` with `CONTEXT_GRAPH_PROJECT_DIR=${workspaceFolder}` |
| `.cursor/hooks.json` | a `sessionStart` hook: `<python> -m context_graph.cli hook session-start --format cursor` |
| `.cursor/rules/context-graph.mdc` | an `alwaysApply: true` rule with the usage policy |

Restart Cursor, and enable the `context-graph` server under **Settings → MCP** if Cursor asks.
`context-graph uninstall cursor` removes exactly these entries.

Good to know:
- **Machine-specific paths.** The paths written to `.cursor/` belong to your machine. Each teammate
  runs `context-graph install cursor` themselves. You probably want `.cursor/mcp.json` and
  `.cursor/hooks.json` out of version control.
- **Updates.** Run `uv tool upgrade context-graph`, then rerun `context-graph install cursor` if
  the interpreter path changed.
- **No `uvx`.** The installer refuses to run from an ephemeral `uvx` environment, because uv may
  delete it and the configs would then point at nothing.

## Start the database

Once per machine, run `/context-graph:setup` in Claude Code, or `context-graph up` in a terminal.
It does the following:

1. Generates a random password into `~/.context-graph/neo4j-password` (192 bits, mode `0600`
   where supported), unless the file already exists.
2. Starts the bundled container with `docker compose -f <package>/assets/compose.yml up -d`.
3. Waits up to 180 seconds for Neo4j to accept connections.
4. Creates the constraints and indexes. This step is idempotent.

The container restarts with Docker, and the data lives in a Docker volume. For details, see
[Operations → Local database](operations.md#local-database).

## External Neo4j (no Docker)

Any Neo4j 5 works, for example a free [Neo4j Aura](https://neo4j.com/cloud/aura-free/) instance or
a team server. Point the client at it, then create the schema once:

| Client | Where to set the connection |
|---|---|
| Claude Code | plugin options in `/config`: `neo4j_uri` (e.g. `neo4j+s://xxxx.databases.neo4j.io`), `neo4j_user`, `neo4j_password` |
| Cursor | `env` of `context-graph` in `.cursor/mcp.json`: `CONTEXT_GRAPH_NEO4J_URI`, `CONTEXT_GRAPH_NEO4J_USER`, `CONTEXT_GRAPH_NEO4J_PASSWORD` |

```sh
CONTEXT_GRAPH_NEO4J_URI=neo4j+s://… CONTEXT_GRAPH_NEO4J_PASSWORD=… context-graph init-schema
```

Everyone pointed at a shared instance can read and write every node, because there is no
per-user access control. See [Operations → Security](operations.md#security).

## Verify

```
/context-graph:status        # Claude Code
context-graph status         # terminal
```

```
Neo4j: bolt://127.0.0.1:7688  database=neo4j  user=neo4j
Status: online, N nodes
Project for /path/to/your/project: <project-id>
```

Then start a new session and check:
- Its context opens with `# Context graph: …`, which is injected by the hook.
- In Claude Code, `/mcp` lists `plugin:context-graph:context-graph` as connected. In Cursor, the
  `context-graph` tools appear under **Settings → MCP**.

To browse the graph, open **http://127.0.0.1:7475** and connect with:
- URL: `bolt://127.0.0.1:7688`
- user: `neo4j`
- password: the contents of `~/.context-graph/neo4j-password`

If something is off, see [Troubleshooting](troubleshooting.md).
