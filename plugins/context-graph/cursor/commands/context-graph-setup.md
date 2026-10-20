---
name: context-graph-setup
description: Start the context graph's local Neo4j database in Docker, create its schema and prepare semantic search. Run once per machine, or whenever the graph is reported offline.
---

# Set up the context graph

1. Check the prerequisites by running `docker info --format '{{.ServerVersion}}'` and
   `uv --version` in the terminal.
   - If Docker isn't running, ask the user to start Docker Desktop (or the Docker daemon) and stop
     here.
   - If `uv` is missing, point the user to https://docs.astral.sh/uv/getting-started/installation/
     and stop here.

2. Start the database, create the schema and prepare the embedding model. The first run
   downloads the `neo4j:5.26-community` image (~600 MB) and the multilingual model (~220 MB),
   which can take a few minutes:

   ```bash
   uvx --from "git+https://github.com/matinebdi/context-graph#subdirectory=plugins/context-graph" context-graph up
   ```

   - The command creates a random password in `~/.context-graph/neo4j-password` if one doesn't
     exist yet.
   - Neo4j listens on `127.0.0.1` only: bolt on 7688, browser on 7475.

3. Check the result:

   ```bash
   uvx --from "git+https://github.com/matinebdi/context-graph#subdirectory=plugins/context-graph" context-graph status
   ```

4. Tell the user:
   - The graph is ready.
   - They can browse it at http://127.0.0.1:7475 with user `neo4j` and the password in
     `~/.context-graph/neo4j-password`.
   - If the `context-graph` tools reported errors earlier, the next tool call reconnects by itself.
