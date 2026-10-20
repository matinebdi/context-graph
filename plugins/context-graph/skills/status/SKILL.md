---
name: status
description: Show whether the context graph database is reachable, how many nodes it holds by type, and which project node this directory maps to.
disable-model-invocation: true
allowed-tools: Bash
---

Run the following command and report the result briefly:

```bash
UV_PROJECT_ENVIRONMENT="${CLAUDE_PLUGIN_DATA}/venv" uv run --quiet --frozen --project "${CLAUDE_PLUGIN_ROOT}" context-graph status
```

If it reports OFFLINE, suggest `/context-graph:setup`.
