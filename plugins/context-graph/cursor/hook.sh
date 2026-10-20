#!/usr/bin/env sh
# Cursor plugin hook: runs the context-graph CLI from this plugin's own Python environment.
# Usage: hook.sh session-start|stop   (the hook JSON arrives on stdin)
# The plugin root is derived from this script's location, so the working directory doesn't matter.
root="$(cd "$(dirname "$0")/.." && pwd)"

if command -v uv >/dev/null 2>&1; then
  exec uv run --quiet --frozen --project "$root" python -m context_graph.cli hook "$1" --format cursor
fi

if [ "$1" = "session-start" ]; then
  printf '%s\n' '{"additional_context": "[context-graph] The context graph is unavailable: uv is not installed (or not on PATH). Tell the user to install uv (https://docs.astral.sh/uv/getting-started/installation/) and restart Cursor."}'
else
  printf '{}\n'
fi
