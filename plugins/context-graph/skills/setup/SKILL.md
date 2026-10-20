---
name: setup
description: Start the context graph's local Neo4j database in Docker and initialise its schema. Run once per machine, or whenever the graph is reported offline.
disable-model-invocation: true
allowed-tools: Bash
---

# Set up the context graph

1. Check the prerequisites by running `docker info --format '{{.ServerVersion}}'` and
   `uv --version`.
   - If Docker isn't running, ask the user to start Docker Desktop (or the Docker daemon) and stop
     here.
   - If `uv` is missing, point the user to https://docs.astral.sh/uv/getting-started/installation/
     and stop here.

2. Start the database and create the schema. The first run downloads the Python dependencies and
   the `neo4j:5.26-community` image, which can take a few minutes.

   ```bash
   UV_PROJECT_ENVIRONMENT="${CLAUDE_PLUGIN_DATA}/venv" uv run --quiet --frozen --project "${CLAUDE_PLUGIN_ROOT}" context-graph up
   ```

   - The command creates a random password in `~/.context-graph/neo4j-password` if one doesn't
     exist yet.
   - It binds Neo4j to `127.0.0.1` only: bolt on 7688, browser on 7475 (chosen so they never clash with a Neo4j you already run on the default ports).
   - If the ports are taken, rerun with `CONTEXT_GRAPH_BOLT_PORT` / `CONTEXT_GRAPH_HTTP_PORT` set,
     and set the plugin's `neo4j_uri` option to match.

3. Check the result:

   ```bash
   UV_PROJECT_ENVIRONMENT="${CLAUDE_PLUGIN_DATA}/venv" uv run --quiet --frozen --project "${CLAUDE_PLUGIN_ROOT}" context-graph status
   ```

4. Tell the user:
   - The graph is ready.
   - They can browse it at http://localhost:7475 with user `neo4j` and the password in
     `~/.context-graph/neo4j-password`.
   - If the `context-graph` MCP tools reported errors earlier in this session, the next tool call
     reconnects automatically. If it doesn't, they can run `/mcp` and reconnect the server.

To use an external Neo4j instead, such as a shared team instance or Aura, skip step 2. Set the
plugin's `neo4j_uri`, `neo4j_user`, `neo4j_password` and `neo4j_database` options in `/config`,
then run `context-graph init-schema` the same way as step 3.
