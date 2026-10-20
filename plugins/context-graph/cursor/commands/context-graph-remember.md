---
name: context-graph-remember
description: Explicitly record something in the context graph, such as a fact, preference, decision or task the user wants kept.
---

The user wants something remembered in the context graph: the text they gave with this command,
or else the last thing they asked you to keep.

1. Decide what kind of knowledge it is: person, preference, decision, concept, resource, tool
   or task. Decide whether it is global or belongs to the current project.
2. Use `search_graph` to look for an existing node that covers it. Update that node if there is
   one. Otherwise create a new node with `upsert_node`, using a clear id, a one-line summary and
   relations to the nodes it connects to.
3. Confirm in one line what you stored and where. Include the node id.
