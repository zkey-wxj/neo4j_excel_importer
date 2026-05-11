# Neo4j Excel Graph Importer

Import a structured Excel knowledge-graph file into Neo4j with one Dify tool call.

## Supported Excel Format

The plugin auto-detects alternating **node tables** and **relationship tables** in a single sheet:

| Table Type | Required Columns |
|---|---|
| Node | `NodeID` · `name` · `node_type` · `definition` · `level` · `grade_range` · `keywords` · `teaching_tip` |
| Relationship | `SourceID` · `RelationType` · `TargetID` · *(optional)* `说明` |

Section heading rows and blank rows are skipped automatically.

## Credentials

| Field | Description |
|---|---|
| `neo4j_uri` | Bolt URI, e.g. `bolt://localhost:7687` or `neo4j+s://xxxxx.databases.neo4j.io` |
| `neo4j_user` | Database username (default `neo4j`) |
| `neo4j_password` | Database password |

## Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `excel_url` | string | — | Public HTTPS URL of the `.xlsx` file |
| `excel_file` | file | — | Uploaded `.xlsx` file (alternative to URL) |
| `batch_size` | number | `500` | Rows per Neo4j transaction |
| `clear_before_import` | boolean | `false` | Delete all nodes/relationships before importing |

## Output Variables (Workflow)

| Variable | Type | Description |
|---|---|---|
| `nodes_count` | integer | Nodes merged |
| `rels_count` | integer | Relationships merged |
| `skipped_rels` | integer | Relationships skipped (missing endpoints) |
| `node_type_stats` | object | `{node_type: count}` map |
| `rel_type_stats` | object | `{rel_type: count}` map |
| `summary` | string | Human-readable import report |

## Notes

- All writes use `MERGE` — safe to run multiple times (idempotent).
- If APOC is installed, relationships are created with their actual type names (e.g. `[:包含方法]`); otherwise a generic `[:RELATED {relType: "…"}]` edge is used.
