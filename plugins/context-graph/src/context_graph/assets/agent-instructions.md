# Using the context graph

The `context-graph` MCP server is your long-term memory. It is a Neo4j graph shared across
every project where it is enabled, and it is where knowledge outlives the session.

## Recall

- At the start of a task, use the overview your client injected at session start, if there is
  one (Claude Code does this). Otherwise call `graph_overview` first.
- Before asking the user something, `search_graph` for it. If a node looks relevant, `get_node`
  it (use `depth=2` for its neighbourhood).
- For structural questions, such as "which decisions affect X?" or "what is still open?", use
  `query_cypher`. Nodes are `(:Entity:<Type> {id, type, title, summary, body, status, tags})`.
  Project membership is `(n)-[:IN_PROJECT]->(:Project)`.
- Follow `preference` nodes. They describe how this user wants you to work.

## Record (as you go, not only at the end)

| When this happens | Write |
|---|---|
| The user tells you about themselves, their role or goals | update their `person` node (global) |
| The user corrects you or states how they like things done | a `preference` node, linked from the person with `PREFERS` |
| A choice is made: library, architecture, convention, trade-off | a `decision` node with the *why* in `body`, `PART_OF` the project |
| You learn how a component, domain concept or workflow works | a `concept` node, plus `DEPENDS_ON` / `PART_OF` links |
| A URL, doc, dashboard or ticket matters | a `resource` node |
| Work is left open across sessions | a `task` node; set `status='done'` when it is finished |
| Meaningful work is finished | `log_session` with a short summary and the related node ids |

Rules:

1. **Search before creating.** Update an existing node instead of making a near-duplicate.
   Ids are stable lowercase-dash slugs with a type prefix where it helps, such as
   `decision-use-postgres` or `pref-small-commits`.
2. **Keep `summary` to one line.** Details go in `body`. Use `append_body` to add dated notes
   without rewriting.
3. **Link generously but meaningfully.** A node with no relations is hard to find later.
   Suggested relation types are `WORKS_ON`, `PREFERS`, `DECIDED`, `PART_OF`, `DEPENDS_ON`,
   `USES`, `RELATES_TO`, `SUPERSEDES`, `ABOUT`, `DOCUMENTED_IN`, `BLOCKS`.
4. **Supersede, don't contradict.** When a fact or decision changes, set the old node's
   `status='superseded'` and link the new node to it with `SUPERSEDES`.
5. **Scope.** New nodes attach to the current project automatically. `person`, `preference`,
   `tool` and `project` nodes are global. Pass `scope='global'` for knowledge that applies
   everywhere.
6. **Never store** secrets, credentials, tokens, or personal data the user didn't choose to
   share.
7. **Write quietly.** Recording is routine, so don't narrate every write. Mention it only when
   you store something the user might not expect.

If a tool reports that the graph is offline, tell the user once how to start it, then carry on
without it:
- in Claude Code: `/context-graph:setup`
- anywhere else: `context-graph up` in a terminal
