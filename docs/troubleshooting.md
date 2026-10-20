# Troubleshooting

[← README](../README.md)

- [Session start messages](#session-start-messages)
- [Database](#database)
- [Neo4j Browser](#neo4j-browser)
- [Claude Code](#claude-code)
- [Cursor](#cursor)
- [Windows](#windows)
- [Logs](#logs)

## Session start messages

| Message | Cause | Fix |
|---|---|---|
| `[context-graph] … uv is not installed` | `uv` isn't on Claude Code's `PATH`, so the MCP server can't start either | install uv, restart Claude Code, run `/context-graph:setup` |
| `… Docker is not installed` | the local database is configured, but there is no `docker` command | install Docker Desktop and run setup, **or** use an [external Neo4j](installation.md#external-neo4j-no-docker) |
| `[context-graph] The context graph is offline (…)` | Docker or the container isn't running, or setup never ran | `/context-graph:setup` or `context-graph up` (or `docker start context-graph-neo4j`) |
| tools say `no Neo4j password configured` | setup never ran on this machine | run setup; the next tool call reconnects by itself |

## Database

| Symptom | Cause | Fix |
|---|---|---|
| `port is already allocated` on 7475 or 7688 | another program uses the port | `CONTEXT_GRAPH_BOLT_PORT=7690 CONTEXT_GRAPH_HTTP_PORT=7476 context-graph up`, then set the URI to `bolt://127.0.0.1:7690` |
| `Neo4j rejected the credentials` after a reinstall | the data volume was created with an earlier password, which Neo4j keeps | restore the old `~/.context-graph/neo4j-password`, **or** (this deletes the graph) `docker compose -p context-graph down -v`, then `context-graph up` |

## Neo4j Browser

| Symptom | Cause | Fix |
|---|---|---|
| *Could not perform discovery. No routing servers available* | the browser remembers an old connection on port 7687, or `localhost` resolves to IPv6 | open `http://127.0.0.1:7475/browser/?connectURL=bolt://neo4j@127.0.0.1:7688`, or clear the site data for that page |

## Claude Code

| Symptom | Cause | Fix |
|---|---|---|
| the first session takes a while to start | `uv` is installing the dependencies, which happens once | wait; the hook allows 120 s. `/context-graph:status` pre-warms it |
| `/mcp` shows the server as failed | dependency install failed, no network, or Python download blocked | run `uv run --frozen --project <plugin-root> python -m context_graph.server` to see the error |

## Cursor

| Symptom | Cause | Fix |
|---|---|---|
| no `context-graph` tools | the server is disabled, or Python moved after an upgrade | enable it in **Settings → MCP**, rerun `context-graph install cursor`, restart Cursor |
| the overview shows the wrong project | `.cursor/` isn't at the project root | run `context-graph install cursor` in the project root |
| `install` refuses: *temporary uvx environment* | it was run through `uvx`, whose environment can disappear | install it properly first: `uv tool install "git+https://github.com/matinebdi/context-graph#subdirectory=plugins/context-graph"` |

## Windows

| Symptom | Cause | Fix |
|---|---|---|
| `[WinError 4551] An Application Control policy has blocked this file` | Smart App Control blocks newly generated, unsigned `.exe` launchers | use `python -m context_graph.cli …`. The configs this project writes already do |

## Logs

| Component | Where |
|---|---|
| Claude Code hooks | `claude --debug` |
| MCP server | the client's MCP logs |
| Neo4j | `docker logs context-graph-neo4j` |
