# Operations

[← README](../README.md)

- [Local database](#local-database)
- [Backup and migration](#backup-and-migration)
- [Security](#security)
- [Uninstall](#uninstall)

## Local database

The container is defined in `src/context_graph/assets/compose.yml` (Compose project
`context-graph`). The file ships inside the Python package, so `context-graph up` works from any
install.

| Setting | Value | Why |
|---|---|---|
| image | `neo4j:5.26-community` | 5.26 is a long-term support release |
| container | `context-graph-neo4j` | a fixed name for `docker logs` / `docker exec` |
| ports | `127.0.0.1:7475→7474` (browser), `127.0.0.1:7688→7687` (bolt) | loopback only; non-default ports avoid clashing with a Neo4j you may already run |
| advertised bolt address | `127.0.0.1:7688` | clients such as Neo4j Browser are told the host-side port, not the container's 7687 |
| password | from `~/.context-graph/neo4j-password` | Compose refuses to start without it |
| memory | heap 512 MB → 1 GB, page cache 512 MB | sized for a personal graph |
| restart | `unless-stopped` | the database comes back with Docker |
| data | Docker volume `context-graph_neo4j-data` | survives container re-creation |

> The password is only applied when the volume is **first created**. Changing or losing the
> password file afterwards doesn't change the database password. See
> [Troubleshooting](troubleshooting.md).

`127.0.0.1` is used everywhere instead of `localhost`. The ports are published on IPv4 only, and
on Windows `localhost` may resolve to the IPv6 `::1` first.

Useful commands:

```sh
context-graph down                     # stop; data is kept
context-graph up                       # start again
docker logs -f context-graph-neo4j     # server logs
docker exec -it context-graph-neo4j cypher-shell -u neo4j -p "$(cat ~/.context-graph/neo4j-password)"
```

## Backup and migration

```sh
context-graph export ~/context-graph-backup.json
context-graph import ~/context-graph-backup.json
```

The export format is:

```json
{ "format": "context-graph", "version": 1,
  "nodes": [ { "id": "…", "type": "…", "title": "…", "summary": "…", "body": "…",
               "status": "active", "path": null, "tags": [],
               "relations": [ { "type": "PART_OF", "target": "…", "note": "…" } ],
               "projects": [ "…" ], "created": "…", "updated": "…" } ] }
```

How an import behaves:
- It runs in a single transaction. If a relation points at a node that isn't in the file, nothing
  is imported.
- Existing nodes are overwritten, nothing is deleted, and timestamps are kept.

**Moving to another Neo4j** (Aura, a team server):
1. Export the graph.
2. Change the connection settings ([Installation → External Neo4j](installation.md#external-neo4j-no-docker)).
3. Run `context-graph init-schema`.
4. Import the file.

For a raw backup of the local database, use `neo4j-admin database dump` inside the stopped
container, or snapshot the `context-graph_neo4j-data` volume.

## Security

| Concern | How it is handled |
|---|---|
| Network exposure | the ports listen on `127.0.0.1` only |
| Credentials | a random password per machine in `~/.context-graph/neo4j-password` (mode 0600 where supported). An external database password goes in the OS credential store. No credential is written to the repo or to `settings.json` |
| Cypher injection | values are always query parameters. Labels and relationship types, which can't be parameters, are validated against strict patterns (`Schema.label`, `Schema.relationship`) first. Ids are validated too |
| Destructive queries from the agent | `query_cypher` runs read-only and Neo4j rejects writes. Deletion is only possible node by node, with `delete_node` |
| Sensitive data | the usage policy tells agents never to store secrets, tokens, passwords, or personal data the user didn't choose to share |
| Search input | Lucene operators are stripped before the full-text query runs |

The graph is **personal and local by default**. If several people point at one shared Neo4j,
everyone can read and write every node, because there is no per-user access control.

## Uninstall

```sh
claude plugin uninstall context-graph@context-graph   # Claude Code (--keep-data keeps its Python env)
context-graph uninstall cursor --dir <project>        # Cursor, per project
context-graph down                                    # stop the database
uv tool uninstall context-graph                       # remove the CLI (after `down`)
docker rm context-graph-neo4j                         # remove the container
docker volume rm context-graph_neo4j-data             # DELETES THE GRAPH: export it first
rm -r ~/.context-graph                                # password, embedding model, hook state
claude plugin marketplace remove context-graph
```
