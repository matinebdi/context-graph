# Reference

[← README](../README.md)

- [MCP tools](#mcp-tools)
- [CLI](#cli)
- [Configuration](#configuration)

## MCP tools

The server is named `context-graph`. In Claude Code, `/mcp` lists it as
`plugin:context-graph:context-graph`. Tools return markdown or JSON text. Failures come back as MCP
tool errors with a readable message, such as `Error executing tool link: node(s) do not exist:
['x']`.

| Tool | Parameters | What it does |
|---|---|---|
| `graph_overview` | `all_projects = false` | overview of the current project plus global nodes, or of the whole graph |
| `search_graph` | `query`, `type?`, `limit = 10` | hybrid search by meaning (any language) and keywords; other projects' nodes rank lower ([details](data-model.md#search)) |
| `get_node` | `id`, `depth = 1` | properties, body, relations, and members (for a project); `depth=2` adds second-degree neighbors |
| `context_for_directory` | `path` | the project node(s) containing that directory |
| `upsert_node` | `id`, `type?`, `title?`, `summary?`, `body?`, `append_body?`, `tags?`, `relations?`, `path?`, `status?`, `scope = "auto"` | creates a node (needs `type`) or merges into one; only the fields given change. `tags` replaces the tags; `relations` adds to the relations. Atomic. On create, names similar existing nodes |
| `link` | `source`, `relation`, `target`, `note?` | adds a relationship; both nodes must exist |
| `unlink` | `source`, `relation`, `target` | removes a relationship |
| `set_node_project` | `id`, `project?`, `remove = false` | attaches the node to a project (default: the current one), or detaches it |
| `delete_node` | `id` | deletes the node and its relationships. Prefer `status: archived` or `superseded` for anything with history |
| `merge_nodes` | `keep`, `remove` | folds a duplicate into `keep`: relations, project links, tags and text move over, then `remove` is deleted |
| `log_session` | `summary`, `details?`, `related?` | records a `session-YYYY-MM-DD-<project>` node linked to the project and the related nodes |
| `review_graph` | `stale_days = 30` | reports likely duplicates, orphans, stale tasks, outdated dependencies, missing embeddings |
| `query_cypher` | `cypher`, `params?` | read-only Cypher; up to 100 rows as JSON |

## CLI

| Install type | How to run |
|---|---|
| uv tool | `context-graph <command>` |
| repo clone | `uv run context-graph <command>` inside `plugins/context-graph/` |

`python -m context_graph.cli <command>` is equivalent, and works where console-script launchers
are blocked.

| Command | Description |
|---|---|
| `up` | create the password if needed, start the container, wait for Neo4j, create the schema, prepare the embedding model and embed nodes that lack a vector |
| `down` | stop the container; the data is kept |
| `status [--dir D]` | connection, node counts by type, and the project mapped to `D` |
| `install <client> [--dir D]` | wire a client into project `D`: `cursor` writes `.cursor/`; `claude` prints the plugin commands |
| `uninstall <client> [--dir D]` | remove exactly what `install` added |
| `init-schema` | create constraints and indexes (idempotent) |
| `overview [--all]` | the overview the hook injects; `--all` ignores the current project |
| `show <id> [--depth 2]` | a node page |
| `search <query> [--type T]` | hybrid search |
| `review [--stale-days N]` | report duplicates, orphans, stale tasks, outdated dependencies |
| `reindex [--all]` | compute missing embeddings; `--all` recomputes every node (e.g. after a model change) |
| `generate-agent-files` | development: regenerate the Claude skill and the Cursor plugin rule from `agent-instructions.md` |
| `export <file.json>` | dump the whole graph ([format](operations.md#backup-and-migration)) |
| `import <file.json>` | load an export in one transaction |
| `import-markdown <dir>` | import the legacy one-markdown-file-per-node format |
| `hook session-start\|stop [--format claude\|cursor]` | client hook entry points (read the hook JSON on stdin) |

Exit codes: `0` on success, and `1` on an error or a failed `docker compose`. `hook` always exits
`0`.

## Configuration

### Claude Code plugin options

| Option | Default | Notes |
|---|---|---|
| `neo4j_uri` | `bolt://127.0.0.1:7688` | any Bolt URI: `bolt://`, `neo4j://`, `neo4j+s://` (Aura) |
| `neo4j_user` | `neo4j` | |
| `neo4j_password` | *(empty)* | kept in the OS credential store, not in `settings.json`. Leave it empty for the local database |
| `neo4j_database` | `neo4j` | Community Edition has a single user database, `neo4j` |

### Where each setting comes from

For each setting, the first of these sources that holds a real value wins. A literal `${…}`
placeholder doesn't count, which matters because an unset plugin option can arrive as the text
`${user_config.neo4j_password}`.

1. `CONTEXT_GRAPH_NEO4J_URI` / `_USER` / `_PASSWORD` / `_DATABASE`: set by the client config, or
   by you.
2. `CLAUDE_PLUGIN_OPTION_NEO4J_*`: exported by Claude Code to plugin hooks.
3. For the password only: `~/.context-graph/neo4j-password`.
4. The defaults in the table above.

### Environment variables

| Variable | Default | Effect |
|---|---|---|
| `CONTEXT_GRAPH_NEO4J_URI`, `_USER`, `_PASSWORD`, `_DATABASE` | see above | connection settings |
| `CONTEXT_GRAPH_HOME` | `~/.context-graph` | folder of the password file, the embedding model (`models/`) and the stop-hook state (`sessions/`) |
| `CONTEXT_GRAPH_CLIENT` | `cli` | client name recorded as `created_by` / `updated_by`; the client configs set `claude-code` or `cursor` |
| `CONTEXT_GRAPH_EMBEDDINGS` | on | `off` disables semantic search (keyword search only) |
| `CONTEXT_GRAPH_BOLT_PORT` | `7688` | host bolt port of the local container; update the URI to match |
| `CONTEXT_GRAPH_HTTP_PORT` | `7475` | host port of Neo4j Browser |
| `CONTEXT_GRAPH_PROJECT_DIR` | set by the Cursor configs from `${workspaceFolder}` | the session's project directory; takes precedence over the two below |
| `CLAUDE_PROJECT_DIR` | set by Claude Code (and by Cursor for hooks) | the session's project directory |
| `CURSOR_PROJECT_DIR` | set by Cursor for hooks | the session's project directory |
| `UV_PROJECT_ENVIRONMENT` | `${CLAUDE_PLUGIN_DATA}/venv` | the Claude plugin's Python environment; survives plugin updates |
| `CONTEXT_GRAPH_TEST_URI`, `_TEST_PASSWORD` | — | run the tests against an existing **disposable** database, which they wipe |
