# context-graph

Persistent, cross-project memory for Claude: a graph of typed nodes and relations that Claude
reads and writes through an MCP server. Stored as plain markdown in `graph/nodes/`, one file per
node, and auto-committed to git after every change.

## Model

| Node type    | Used for                                            |
|--------------|-----------------------------------------------------|
| `person`     | the user and people they work with                  |
| `project`    | a codebase or effort (optional `path` for matching) |
| `decision`   | a choice that was made, and why                     |
| `preference` | how the user likes things done                      |
| `concept`    | domain knowledge, architecture                      |
| `resource`   | links, docs, dashboards, tickets                    |
| `tool`       | software and services in use                        |
| `task`       | open work spanning sessions                         |
| `session`    | log of one working session                          |

Relations are directed and snake_case (`works_on`, `decided`, `part_of`, `depends_on`, `uses`,
`prefers`, `supersedes`, `about`, `relates_to`, ...). They live on the source node; incoming edges
are computed.

## MCP tools

`graph_overview`, `search_graph`, `get_node`, `context_for_directory`, `upsert_node`, `link`,
`unlink`, `delete_node`, `log_session`.

## Setup (once, user-wide)

```sh
uv sync

# 1. Register the MCP server for every project
claude mcp add-json -s user context-graph '{"type":"stdio","command":"uv","args":["run","--quiet","--directory","C:/Users/matin/Documents/context-graph","context-graph-mcp"]}'
```

2. Add a SessionStart hook to `~/.claude/settings.json` so each session starts with the overview
   and the current project's node:

```json
"hooks": {
  "SessionStart": [
    { "hooks": [ { "type": "command", "timeout": 20,
      "command": "uv run --quiet --directory C:/Users/matin/Documents/context-graph context-graph session-start" } ] }
  ]
}
```

3. Append `setup/claude-md-snippet.md` to `~/.claude/CLAUDE.md`.

4. Optional: add `"mcp__context-graph"` to `permissions.allow` so graph reads and writes do not prompt.

## CLI

```sh
uv run context-graph overview
uv run context-graph show <id> --depth 2
uv run context-graph search "<query>" [--type decision]
uv run context-graph check        # relations pointing at missing nodes
uv run pytest
```

`CONTEXT_GRAPH_DIR` overrides the data directory (default: `graph/` in this repo).
