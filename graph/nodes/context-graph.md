---
id: context-graph
type: project
title: Context graph (Claude long-term memory)
summary: MCP server + markdown/git store that gives Claude persistent, cross-project memory of the user
status: active
path: C:/Users/matin/Documents/context-graph
tags:
- mcp
- memory
- python
relations:
- type: uses
  target: claude-code
created: 2026-10-20T10:56+0200
updated: 2026-10-20T10:56+0200
---

Python package `context_graph` (uv project, mcp SDK 2.x `MCPServer`).
- Nodes: one markdown file each in `graph/nodes/<id>.md` (YAML frontmatter + body); relations stored on the source node.
- Every write auto-commits the `graph/` dir to git, so history and undo come for free.
- Registered as user-scope MCP server `context-graph`; a SessionStart hook (`context-graph session-start`) injects the overview + current-project context.
- Data dir override: env `CONTEXT_GRAPH_DIR`.
