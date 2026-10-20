# Data model

[← README](../README.md)

- [Nodes](#nodes)
- [Relationships](#relationships)
- [Project scoping](#project-scoping)
- [Schema](#schema)
- [Search](#search)
- [Duplicates and merging](#duplicates-and-merging)
- [Overview](#overview)
- [Querying with Cypher](#querying-with-cypher)

## Nodes

Every node has the label `:Entity` plus one type label, which is the PascalCase form of its `type`:
`decision` becomes `:Decision`, and `user_story` becomes `:UserStory`.

| Property | Type | Notes |
|---|---|---|
| `id` | string | unique; lowercase letters, digits and dashes, max 80 (`^[a-z0-9][a-z0-9-]{0,79}$`) |
| `type` | string | snake_case (`^[a-z][a-z0-9_]{0,39}$`); mirrors the type label |
| `title` | string | human-readable name |
| `summary` | string | one line, shown in every listing |
| `body` | string | markdown; `append_body` adds dated `**YYYY-MM-DD:** …` paragraphs |
| `status` | string | `active` · `done` · `superseded` · `archived` |
| `tags` | list of strings | lowercase, de-duplicated, sorted |
| `tags_text` | string | the tags joined with spaces, for the full-text index |
| `path` | string | filesystem root (projects only) |
| `path_key` | string | normalized `path`: forward slashes, case-folded on Windows, Git Bash `/c/…` mapped to `c:/…` |
| `created`, `updated` | string | UTC, `YYYY-MM-DDTHH:MM:SSZ`, so they sort as text |
| `created_by`, `updated_by` | string | the client that wrote the node: `claude-code`, `cursor` or `cli` |
| `use_count`, `last_used` | int, string | how often and when search or `get_node` returned the node; used for importance |
| `embedding`, `embedding_model` | list of 384 floats, string | the node's meaning vector for semantic search, and the model that produced it |

Default types (any other snake_case type is accepted too):

| `type` | Default scope | Used for |
|---|---|---|
| `person` | global | the user and the people they work with |
| `preference` | global | how the user wants things done; the overview lists these as rules to follow |
| `tool` | global | software, services, libraries |
| `project` | global | a codebase or effort, matched to directories by `path` |
| `decision` | project | a choice that was made, with its rationale |
| `concept` | project | domain knowledge, architecture, components |
| `resource` | project | URLs, docs, dashboards, tickets |
| `task` | project | open work that spans sessions |
| `session` | project | a log of one working session (`log_session`) |
| *custom* | project | anything else |

## Relationships

- **Types.** Relationships are real Neo4j relationship types in upper snake case. They are
  accepted in any case (`works_on` is stored as `WORKS_ON`) and must match
  `^[A-Za-z][A-Za-z0-9_]{0,39}$`. Each one can carry a `note`.
- **Suggested vocabulary:** `WORKS_ON`, `OWNS`, `PREFERS`, `DECIDED`, `PART_OF`, `DEPENDS_ON`,
  `USES`, `RELATES_TO`, `SUPERSEDES`, `ABOUT`, `DOCUMENTED_IN`, `BLOCKS`, `FOLLOWS_UP`.
- **Reserved:** `IN_PROJECT` is used for scoping. `link` rejects it, and it is left out of the
  relation listings.
- **Behavior.** Relationships are directed and de-duplicated on `(source, type, target)`.
  Linking again only updates the note, and only if a new one is given.

## Project scoping

The `scope` argument of `upsert_node` decides whether the node gets an `IN_PROJECT` link to the
current project:

| `scope` | Behavior |
|---|---|
| `auto` (default) | link it, unless its type is `person`, `preference`, `tool` or `project` |
| `project` | always link it |
| `global` | never add a link (existing links stay; remove them with `set_node_project`) |

A node can belong to several projects. A node with no `IN_PROJECT` link is global.

Example:

```cypher
(:Entity:Person   {id:'alice'})-[:WORKS_ON]->(:Entity:Project {id:'shop-api', path:'/home/alice/shop-api'})
(:Entity:Person   {id:'alice'})-[:PREFERS]->(:Entity:Preference {id:'pref-small-commits'})
(:Entity:Decision {id:'decision-use-postgres'})-[:PART_OF]->(:Project {id:'shop-api'})
(:Entity:Decision {id:'decision-use-postgres'})-[:SUPERSEDES]->(:Decision {id:'decision-use-sqlite', status:'superseded'})
(:Entity:Decision {id:'decision-use-postgres'})-[:IN_PROJECT]->(:Project {id:'shop-api'})
```

## Schema

`context-graph up` and `context-graph init-schema` apply these statements. They are safe to run
more than once:

```cypher
CREATE CONSTRAINT entity_id IF NOT EXISTS FOR (n:Entity) REQUIRE n.id IS UNIQUE;
CREATE INDEX entity_type    IF NOT EXISTS FOR (n:Entity)  ON (n.type);
CREATE INDEX entity_updated IF NOT EXISTS FOR (n:Entity)  ON (n.updated);
CREATE INDEX project_path   IF NOT EXISTS FOR (n:Project) ON (n.path_key);
CREATE FULLTEXT INDEX entity_text IF NOT EXISTS
  FOR (n:Entity) ON EACH [n.id, n.title, n.summary, n.body, n.tags_text];
CREATE VECTOR INDEX entity_embedding IF NOT EXISTS FOR (n:Entity) ON n.embedding
  OPTIONS {indexConfig: {`vector.dimensions`: 384, `vector.similarity_function`: 'cosine'}};
```

## Search

`search_graph` is hybrid: it combines a keyword ranking and a meaning ranking.

1. **Keyword ranking.** The query is split into words, which drops every Lucene operator. Each
   word `w` becomes `w OR w*`, so `"rotate jwt"` becomes `rotate OR rotate* OR jwt OR jwt*`. That
   query runs against the full-text index `entity_text` (id, title, summary, body, tags).
2. **Meaning ranking.** The query is embedded with the same local model as the nodes, and the
   vector index returns the nearest nodes. Matches below a cosine similarity of `0.3` are dropped.
3. **Fusion.** The two rankings are merged with *reciprocal rank fusion*: each list adds
   `1 / (60 + rank)` to a node's score. A node found by both rankings wins, and the two score
   scales never need calibrating.
4. **Scope.** Nodes that belong only to *other* projects are ranked as if found 5 places lower,
   and inactive nodes 10 places lower. Nodes of the current project and global nodes (people,
   preferences, tools) aren't penalized.

**The model.** `paraphrase-multilingual-MiniLM-L12-v2` (384 dimensions, Apache-2.0), run locally
with [fastembed](https://github.com/qdrant/fastembed) (ONNX). It understands about 50 languages
and matches across them: *"mot de passe de la base"* finds a note about *"Neo4j password"*. A
node's vector is computed from its title, summary, tags and the first 1,500 characters of its
body, and recomputed whenever they change. If the model can't load, search silently falls back
to keywords only.

## Duplicates and merging

- **On create.** When a new node's meaning is very close to an existing node of the same type
  (cosine ≥ 0.8), `upsert_node` names the similar nodes in its reply.
- **Merging.** `merge_nodes(keep, remove)` moves the removed node's relations (both directions)
  and project links onto `keep`, unites the tags, appends its summary and body to `keep`, then
  deletes it.
- **Review.** `review_graph` / `context-graph review` reports:
  - likely duplicates (same type, cosine ≥ 0.85)
  - orphans (nodes without any relation)
  - stale tasks (active, not updated for 30 days)
  - superseded nodes that active nodes still depend on
  - nodes missing an embedding

## Overview

This is what the session-start hook injects and what `graph_overview` returns. Sections are
filled in this order, each with its most **important** nodes first, until the overview reaches
**6,000 characters** (about 1,500 tokens). Each section then says how many nodes it left out,
and a node appears in one section at most.

| Section | Contents | Max |
|---|---|---|
| Preferences | active, global or in the current project | 25 |
| People | active, global or in the current project | 10 |
| Open tasks in *project* | active tasks of the current project | 15 |
| Decisions in *project* | active decisions of the current project | 15 |
| Recent sessions in *project* | the latest sessions, by date | 3 |
| Projects | active projects | 10 |
| Recently updated | the latest changes graph-wide, by date | 8 |

**Importance** = `ln(1 + relationships) + ln(1 + uses) + 2 × 0.5^(age in days / 30)`. A
well-connected node that agents keep reading ranks high even when it's old, and a fresh node
gets a head start that halves every 30 days.

## Querying with Cypher

`query_cypher` runs any Cypher in a **read** transaction. Neo4j itself rejects writes
(`Writing in read access mode not allowed`). It returns up to 100 rows as JSON, and converts
dates and other special values to text.

```cypher
// open tasks across all projects
MATCH (t:Task {status:'active'})-[:IN_PROJECT]->(p:Project) RETURN p.id, t.id, t.title ORDER BY p.id;

// decision history chains
MATCH path = (d:Decision)-[:SUPERSEDES*]->(old:Decision) RETURN [n IN nodes(path) | n.id] AS chain;

// nodes with no relations at all
MATCH (n:Entity) WHERE NOT (n)--() RETURN n.id, n.type;

// what depends on a component
MATCH (x:Entity)-[:DEPENDS_ON*1..3]->(c:Entity {id:$id}) RETURN DISTINCT x.id, x.type;
```
