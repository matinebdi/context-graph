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
claude plugin validate .          # plugin manifest, hooks, MCP config
claude plugin validate ../..      # marketplace
```

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
| `tests/integration/test_neo4j_store.py` | upsert and relabeling, transactional rollback, link de-duplication, delete cascade, project scoping, search ranking, read-only Cypher, export/import round trip, rendering, server tool methods |

How the integration tests get their database:
- They run against a **throwaway** container, `context-graph-test-neo4j` on `127.0.0.1:17687`.
  The `ThrowawayNeo4j` class in `tests/integration/conftest.py` starts and removes it.
- They never touch your real graph, and they are skipped when Docker isn't available.
- To use an existing disposable database instead, set `CONTEXT_GRAPH_TEST_URI` and
  `CONTEXT_GRAPH_TEST_PASSWORD`. The tests wipe that database.

## Repository layout

```
.
├── .claude-plugin/marketplace.json   # marketplace "context-graph" → ./plugins/context-graph
├── .claude/settings.json             # enables the plugin for this repo (dogfooding)
├── docs/                             # these guides
├── LICENSE · README.md
└── plugins/context-graph/            # the plugin root (${CLAUDE_PLUGIN_ROOT}) and the Python package
    ├── .claude-plugin/plugin.json    # manifest + userConfig
    ├── .mcp.json                     # MCP server via uv
    ├── hooks/hooks.json              # SessionStart hook
    ├── skills/                       # context-graph (policy), setup, status, remember
    ├── pyproject.toml · uv.lock
    ├── src/context_graph/
    │   ├── domain/                   # Node, Relation, Schema, Clock, PathKey, GraphError
    │   ├── config/                   # Environment, LocalCredentials, Settings
    │   ├── storage/                  # Neo4jStore, Neo4jErrorTranslator
    │   ├── rendering/                # MarkdownRenderer
    │   ├── server/                   # ContextGraphServer
    │   ├── cli/                      # ContextGraphCLI, SessionStartHook, LocalDatabase
    │   ├── integrations/             # ClientIntegration, CursorIntegration, ClaudeCodeIntegration
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
- `assets/agent-instructions.md` is the single source of the usage policy. If you edit it,
  regenerate `skills/context-graph/SKILL.md` (frontmatter + that text), or the sync test fails.

## Releasing

1. Bump `version` in both `plugins/context-graph/.claude-plugin/plugin.json` and `pyproject.toml`.
   Claude Code keeps users on the manifest version, so they only get updates when it changes.
2. If dependencies changed, run `uv lock`. Installs use `--frozen`, so they get exactly what is
   in `uv.lock`.
