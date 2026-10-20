# Context graph (long-term memory)

You have a persistent context graph through the `context-graph` MCP server. It is shared across
all projects and sessions with me, and it is the primary place to remember things about me.

- At the start of work, use the context the SessionStart hook injected; if it is missing, call
  `graph_overview` and `context_for_directory` with the current directory.
- Before asking me something I may already have told you, `search_graph` / `get_node`.
- As we work, record durable information: facts about me, my preferences and corrections,
  decisions with their reasons, projects (with `path`), useful resources, open tasks.
  Search first and update existing nodes instead of creating duplicates.
- When a fact changes, update the node, or mark the old decision `status: superseded` and link
  the new one with `supersedes`.
- At the end of meaningful work in a project, call `log_session`.
- Never store secrets, tokens, passwords or credentials.
