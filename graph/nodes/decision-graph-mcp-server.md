---
id: decision-graph-mcp-server
type: decision
title: Expose the graph as a Python MCP server
summary: Python MCP server over plain markdown files tracked in git, rather than a JSON blob or database
status: active
relations:
- type: part_of
  target: context-graph
created: 2026-10-20T10:56+0200
updated: 2026-10-20T10:56+0200
---

Why: MCP makes the graph usable from any Claude client, not only this terminal. Markdown files stay human-readable
and hand-editable, and git gives history and undo. In-memory scans are fine at this scale; switch to SQLite indexing
if the graph grows past a few thousand nodes.
