# Development

[← README](../README.md)

- [Setup](#setup)
- [Tests](#tests)
- [Repository layout](#repository-layout)
- [Code style](#code-style)
- [Releasing](#releasing)

## Setup

```sh
git clone https://github.com/matinebdi/context-graph.git
cd context-graph/plugins/context-graph
uv sync                           # .venv with the dev dependencies
uv run pytest -q                  # all tests
claude plugin validate .          # Claude plugin manifest, hooks, MCP config
claude plugin validate ../..      # Claude marketplace
```

To validate the Cursor plugin, run the validator from Cursor's
[plugin template](https://github.com/cursor/plugin-template) at the repository root:
`node <plugin-template>/scripts/validate-template.mjs`.

To try the Cursor plugin live, copy `plugins/context-graph` into `~/.cursor/plugins/local/` and
reload the window.

To work on the plugin live, add the clone as a marketplace (`claude plugin marketplace add
./context-graph`), edit, then run `/reload-plugins`.

To test the MCP server without a client, use any MCP stdio client. With the Python SDK:

```python
StdioServerParameters(command="uv",
    args=["run", "--frozen", "--project", "plugins/context-graph", "python", "-m", "context_graph.server"],
    env={**os.environ, "CONTEXT_GRAPH_PROJECT_DIR": "/some/project"})
```

## Tests

Tests are grouped in classes per component.

| File | Covers |
|---|---|
| `tests/unit/test_domain.py` | id, type and relation validation, including injection attempts; path normalization; Lucene query building; legacy parser; settings with unresolved placeholders |
| `tests/unit/test_integrations.py` | Cursor installer: merging, idempotency, uninstall, refusing broken JSON and `uvx`; hook command quoting; hook directory precedence; Claude skill in sync with `agent-instructions.md` |
| `tests/unit/test_ranking_and_hooks.py` | rank fusion and penalties; importance scoring (including legacy timestamps); transcript parsing; stop-hook threshold, once-per-stretch behavior and loop guards for both clients |
| `tests/integration/test_neo4j_store.py` | upsert and relabeling, transactional rollback, link de-duplication, delete cascade, project scoping, search ranking, read-only Cypher, export/import round trip, rendering, server tool methods |
| `tests/integration/test_semantic_and_maintenance.py` | semantic matches keywords miss, embeddings following text changes, reindex, provenance, importance ordering, duplicate warnings, merge, review findings, overview budget |

The integration tests use `TrigramEmbedder` (in `conftest.py`) instead of the real model. It is a
deterministic hashed-trigram embedder, so no download is needed and the tests stay fast.

How the integration tests get their database:
- They run against a **throwaway** container, `context-graph-test-neo4j` on `127.0.0.1:17687`.
  The `ThrowawayNeo4j` class in `tests/integration/conftest.py` starts and removes it.
- They never touch your real graph, and they are skipped when Docker isn't available.
- To use an existing disposable database instead, set `CONTEXT_GRAPH_TEST_URI` and
  `CONTEXT_GRAPH_TEST_PASSWORD`. The tests wipe that database.

## Repository layout

```
.
├── .claude-plugin/marketplace.json   # Claude Code marketplace "context-graph" → ./plugins/context-graph
├── .cursor-plugin/marketplace.json   # Cursor marketplace, same plugin folder
├── .claude/settings.json             # enables the plugin for this repo (dogfooding)
├── .gitattributes                    # keeps *.sh with LF line endings on every OS
├── docs/                             # these guides
├── LICENSE · README.md
└── plugins/context-graph/            # plugin root for both clients, and the Python package
    ├── .claude-plugin/plugin.json    # Claude: manifest + userConfig
    ├── .mcp.json                     # Claude: MCP server via uv (${CLAUDE_PLUGIN_ROOT}/_DATA)
    ├── hooks/hooks.json              # Claude: SessionStart and Stop hooks
    ├── skills/                       # Claude: context-graph (policy), setup, status, remember
    ├── .cursor-plugin/plugin.json    # Cursor: manifest with explicit component paths
    ├── mcp.json                      # Cursor: MCP server via uv (${CURSOR_PLUGIN_ROOT})
    ├── cursor/                       # Cursor: rules/, commands/, hooks.json, hook.sh, skills/ (empty)
    ├── assets/logo.svg               # Cursor marketplace logo
    ├── pyproject.toml · uv.lock
    ├── src/context_graph/
    │   ├── domain/                   # Node, Relation, Schema, Clock, PathKey, GraphError
    │   ├── config/                   # Environment, LocalCredentials, Settings
    │   ├── semantic/                 # Embedder
    │   ├── storage/                  # Neo4jStore, GraphMaintenance, Ranking, Neo4jErrorTranslator
    │   ├── rendering/                # MarkdownRenderer
    │   ├── server/                   # ContextGraphServer
    │   ├── cli/                      # ContextGraphCLI, SessionStartHook, StopHook, LocalDatabase
    │   ├── integrations/             # ClientIntegration, CursorIntegration, ClaudeCodeIntegration, AgentFiles
    │   └── assets/                   # agent-instructions.md, compose.yml
    └── tests/
        ├── unit/
        └── integration/
```

To add a client, subclass `ClientIntegration` in `integrations/` and register it in
`integrations/__init__.py`.

## Code style

- Classes over module-level functions; no module docstrings.
- Google-style docstrings on classes and public methods. In `ContextGraphServer`, these are also
  the tool descriptions the agent reads.
- `assets/agent-instructions.md` is the single source of the usage policy. After editing it, run
  `uv run context-graph generate-agent-files` to regenerate the Claude skill and the Cursor rule,
  or the sync test fails.

## Releasing

1. Bump the version in **four** places, which a test keeps in sync for the two manifests:
   - `plugins/context-graph/.claude-plugin/plugin.json`
   - `plugins/context-graph/.cursor-plugin/plugin.json`
   - `pyproject.toml`
   - the `metadata.version` of `.cursor-plugin/marketplace.json`

   Claude Code keeps users on the manifest version, so they only get updates when it changes.
2. If dependencies changed, run `uv lock`. Installs use `--frozen`, so they get exactly what is
   in `uv.lock`.
3. Run the tests and the three validators (two Claude, one Cursor).
4. To publish in the Cursor Marketplace, submit the repository at
   https://cursor.com/marketplace/publish, where each plugin is reviewed by hand. Community
   listings go on https://cursor.directory.
