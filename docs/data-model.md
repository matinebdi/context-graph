# Data model

[← README](../README.md)

- [Nodes](#nodes)
- [Relationships](#relationships)
- [Project scoping](#project-scoping)
- [Schema](#schema)
- [Search](#search)
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
```

## Search

`search_graph` works in four steps:

1. **Words.** The input is split into words and lower-cased. That drops every Lucene operator
   and special character. Each word `w` becomes `w OR w*`, matching it exactly or as a prefix:
   `"rotate jwt"` becomes `rotate OR rotate* OR jwt OR jwt*`.
2. **Index.** The full-text index `entity_text` is queried, covering id, title, summary, body and
   tags.
3. **Ranking.** A node's score is multiplied by `1.5` if it belongs to the current project, and by
   `0.5` if its status isn't `active`.
4. **Result.** Results are optionally filtered by type, sorted by score, and cut to `limit`.

## Overview

This is what the session-start hook injects and what `graph_overview` returns:

| Section | Contents |
|---|---|
| People, Preferences | active nodes that are global or in the current project; up to 25 each |
| Projects | active projects, most recently updated first; up to 10 |
| Open tasks / Decisions in *project* | active nodes in the current project; up to 15 each |
| Recent sessions in *project* | the last 3 sessions |
| Recently updated | the last 8 nodes in the whole graph |

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
