---
name: context-graph-status
description: Show whether the context graph database is reachable, how many nodes it holds by type, and which project node this workspace maps to.
---

Run the following command in the terminal and report the result briefly:

```bash
uvx --from "git+https://github.com/matinebdi/context-graph#subdirectory=plugins/context-graph" context-graph status
```

If it reports OFFLINE, suggest the `/context-graph-setup` command.
