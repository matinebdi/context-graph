# Context Graph

A Claude Code plugin that gives Claude **long-term memory as a knowledge graph**. In every project
where the plugin is enabled, Claude records what it learns into a Neo4j graph: who you are, how you
like to work, what was decided and why, how the project fits together, and what is still open.
Claude recalls that knowledge at the start of the next session.

- **One graph, scoped by project.** Knowledge about you (preferences, tools) follows you
  everywhere. Decisions, concepts and tasks belong to the project they came from.
- **A real graph database.** Neo4j runs locally in Docker, or you can point the plugin at your own
  instance. Browse what Claude knows at http://127.0.0.1:7475 and query it with Cypher.
- **Low friction.** A SessionStart hook loads the relevant context automatically. A skill teaches
  Claude when and what to record.

## Requirements

- [Claude Code](https://code.claude.com)
- [Docker](https://docs.docker.com/get-docker/), for the bundled local database
- [uv](https://docs.astral.sh/uv/getting-started/installation/), which runs the Python MCP server

## Install

Add the marketplace once. Then enable the plugin in each project where you want Claude to use the
graph:

```sh
# from GitHub, once this repo is published
/plugin install context-graph --marketplace <owner>/<repo>

# or from a local clone
claude plugin marketplace add /path/to/context-graph
cd your-project
claude plugin install context-graph@context-graph --scope project
```

`--scope project` writes the plugin to the project's `.claude/settings.json`, so it is on for
that project only, and for your teammates if they commit that file. Use `--scope user` to enable
it everywhere.

Then, once per machine:

```
/context-graph:setup
```

Setup starts `neo4j:5.26-community` in Docker, creates the schema, and generates a password in
`~/.context-graph/neo4j-password`. The database listens on `127.0.0.1` only, on ports **7688**
(bolt) and **7475** (browser). These ports don't collide with a Neo4j you may already run on the
defaults. The container restarts with Docker, and data lives in the `context-graph_neo4j-data`
volume.

## Commands

| Command | What it does |
|---|---|
| `/context-graph:setup` | start the local database and create the schema |
| `/context-graph:status` | check the connection, node counts, and which project this directory maps to |
| `/context-graph:remember <text>` | explicitly store something |

The rest happens automatically. The hook injects an overview and the current project at session
start, and Claude reads and writes the graph through the MCP tools as it works.

## Graph model

Each node has the `:Entity` label plus a type label, and the properties `id`, `type`, `title`,
`summary` (one line), `body` (markdown), `status`, `tags`, `created` and `updated`.

| Type | Used for | Scope by default |
|---|---|---|
| `person` | you and people you work with | global |
| `preference` | how you like things done | global |
| `tool` | software and services in use | global |
| `project` | a codebase, matched to directories by `path` | global |
| `decision` | a choice that was made, and why | project |
| `concept` | domain knowledge, architecture, components | project |
| `resource` | links, docs, dashboards, tickets | project |
| `task` | open work that spans sessions | project |
| `session` | log of one working session | project |

Custom snake_case types are allowed. Relations are real Neo4j relationship types, such as
`WORKS_ON`, `PREFERS`, `DECIDED`, `PART_OF`, `DEPENDS_ON`, `USES`, `RELATES_TO`, `SUPERSEDES`,
`ABOUT`, `DOCUMENTED_IN` and `BLOCKS`. Each one can carry an optional `note`. Project membership
is `(n)-[:IN_PROJECT]->(:Project)`.

**MCP tools:**

| Tool | Purpose |
|---|---|
| `graph_overview` | summary of the graph and the current project |
| `search_graph` | full-text search; current-project results rank higher |
| `get_node` | one node with its relations |
| `context_for_directory` | the project node that matches a directory |
| `upsert_node` | create or update a node |
| `link` / `unlink` | add or remove a relation |
| `set_node_project` | change which project a node belongs to |
| `delete_node` | remove a node |
| `log_session` | record what a working session did |
| `query_cypher` | run Cypher in a read-only transaction |

## Configuration

To use your own Neo4j, such as a team server or Aura, set the plugin options in `/config`:

| Option | Default |
|---|---|
| `neo4j_uri` | `bolt://127.0.0.1:7688` |
| `neo4j_user` | `neo4j` |
| `neo4j_password` | empty, meaning the local password file is used. Stored in secure storage |
| `neo4j_database` | `neo4j` |

Environment overrides:
- `CONTEXT_GRAPH_HOME` moves the password file (default `~/.context-graph`).
- `CONTEXT_GRAPH_BOLT_PORT` and `CONTEXT_GRAPH_HTTP_PORT` change the local container's ports.

## CLI

The plugin's Python package also provides a CLI. From `plugins/context-graph/`:

```sh
uv run context-graph status
uv run context-graph overview [--all]
uv run context-graph show <id> --depth 2
uv run context-graph search "<query>" [--type decision]
uv run context-graph export backup.json      # back up the whole graph
uv run context-graph import backup.json
uv run context-graph up | down               # start/stop the local container
```

## Development

```sh
cd plugins/context-graph
uv sync
uv run pytest     # unit tests, plus integration tests in a throwaway Neo4j container (port 17687)
claude plugin validate .
```

Layout:
- `.claude-plugin/marketplace.json` is the marketplace manifest. It points at `plugins/context-graph/`.
- That folder holds:
  - the plugin manifest
  - `.mcp.json`
  - `hooks/`
  - `skills/`
  - `docker/compose.yml`
  - the `context_graph` Python package: `model`, `config`, `neo4j_store`, `render`, `server`, `cli`
