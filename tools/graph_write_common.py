from __future__ import annotations

from typing import Any

from neo4j import GraphDatabase

from tools.types import NodePayload, RelationPayload

CONSTRAINT_CYPHER = (
    "CREATE CONSTRAINT IF NOT EXISTS "
    "FOR (n:KnowledgeNode) REQUIRE n.nodeId IS UNIQUE"
)

UPSERT_NODES = """
UNWIND $rows AS row
MERGE (n:KnowledgeNode {nodeId: row.nodeId})
SET n.name        = row.name,
    n.nodeType    = row.nodeType,
    n.label       = row.label,
    n.definition  = row.definition,
    n.level       = row.level,
    n.gradeRange  = row.gradeRange,
    n.keywords    = row.keywords,
    n.teachingTip = row.teachingTip
"""

UPSERT_RELS_APOC = """
UNWIND $rows AS row
MATCH (src:KnowledgeNode {nodeId: row.src})
MATCH (tgt:KnowledgeNode {nodeId: row.tgt})
CALL apoc.merge.relationship(src, row.relType, {}, {description: row.desc}, tgt)
YIELD rel RETURN count(rel)
"""

UPSERT_RELS_GENERIC = """
UNWIND $rows AS row
MATCH (src:KnowledgeNode {nodeId: row.src})
MATCH (tgt:KnowledgeNode {nodeId: row.tgt})
MERGE (src)-[r:RELATED {relType: row.relType}]->(tgt)
SET r.description = row.desc
"""


def get_credentials(runtime: Any) -> tuple[str, str, str]:
    uri = str(runtime.credentials.get("neo4j_uri", "")).strip()
    user = str(runtime.credentials.get("neo4j_user", "")).strip()
    pwd = str(runtime.credentials.get("neo4j_password", "")).strip()
    return uri, user, pwd


def has_apoc(session: Any) -> bool:
    try:
        session.run("RETURN apoc.version()").single()
        return True
    except Exception:
        return False


def write_nodes(
    uri: str,
    user: str,
    pwd: str,
    rows: list[NodePayload],
    *,
    batch_size: int,
) -> int:
    driver = GraphDatabase.driver(uri, auth=(user, pwd))
    try:
        with driver.session() as session:
            session.run(CONSTRAINT_CYPHER)
            for start in range(0, len(rows), batch_size):
                batch = rows[start: start + batch_size]
                session.run(UPSERT_NODES, rows=batch)
    finally:
        driver.close()
    return len(rows)


def write_relations(
    uri: str,
    user: str,
    pwd: str,
    rows: list[RelationPayload],
    *,
    batch_size: int,
) -> int:
    driver = GraphDatabase.driver(uri, auth=(user, pwd))
    try:
        with driver.session() as session:
            rel_cypher = UPSERT_RELS_APOC if has_apoc(session) else UPSERT_RELS_GENERIC
            for start in range(0, len(rows), batch_size):
                batch = rows[start: start + batch_size]
                try:
                    session.run(rel_cypher, rows=batch)
                except Exception:
                    session.run(UPSERT_RELS_GENERIC, rows=batch)
    finally:
        driver.close()
    return len(rows)
