---
id: decision-graph-global-scope
type: decision
title: Context graph is global, not per-project
summary: One graph used in every project; registered at user scope and loaded by a global SessionStart
  hook
status: active
relations:
- type: part_of
  target: context-graph
created: 2026-10-20T10:56+0200
updated: 2026-10-20T10:56+0200
---

Why: the user wants Claude to remember them everywhere, not just inside one folder.
