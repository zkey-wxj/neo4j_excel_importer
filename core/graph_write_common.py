from __future__ import annotations

from typing import Any

from neo4j import GraphDatabase

from core.types import NodePayload, RelationPayload, clean_text

CONSTRAINT_CYPHER = (
    "CREATE CONSTRAINT IF NOT EXISTS "
    "FOR (n:KnowledgeNode) REQUIRE n.uid IS UNIQUE"
)

UPSERT_NODES = """
UNWIND $rows AS row
MERGE (n:KnowledgeNode {uid: row.uid})
SET n.name        = row.name,
    n.description = row.description,
    n.group_id    = row.group_id
SET n += row.properties
WITH n, row
CALL apoc.create.addLabels(n, row.labels) YIELD node
RETURN count(node)
"""

UPSERT_NODES_GENERIC = """
UNWIND $rows AS row
MERGE (n:KnowledgeNode {uid: row.uid})
SET n.name        = row.name,
    n.description = row.description,
    n.group_id    = row.group_id
SET n += row.properties
"""

UPSERT_RELS_APOC = """
UNWIND $rows AS row
MATCH (src:KnowledgeNode {uid: row.source_uid})
MATCH (tgt:KnowledgeNode {uid: row.target_uid})
CALL apoc.merge.relationship(src, row.rel_type, {group_id: row.group_id}, row.properties, tgt)
YIELD rel RETURN count(rel)
"""

UPSERT_RELS_GENERIC = """
UNWIND $rows AS row
MATCH (src:KnowledgeNode {uid: row.source_uid})
MATCH (tgt:KnowledgeNode {uid: row.target_uid})
MERGE (src)-[r:RELATED {rel_type: row.rel_type, group_id: row.group_id}]->(tgt)
SET r += row.properties
"""


def get_credentials(runtime: Any) -> tuple[str, str, str]:
    uri = str(runtime.credentials.get("neo4j_uri", "")).strip()
    user = str(runtime.credentials.get("neo4j_user", "")).strip()
    pwd = str(runtime.credentials.get("neo4j_password", "")).strip()
    return uri, user, pwd


def clear_graph(uri: str, user: str, pwd: str) -> None:
    driver = GraphDatabase.driver(uri, auth=(user, pwd))
    try:
        with driver.session() as session:
            session.run("MATCH (n) DETACH DELETE n")
    finally:
        driver.close()


def _has_procedure(session: Any, procedure_name: str) -> bool:
    try:
        record = session.run("RETURN apoc.version() AS version").single()
        if record and clean_text(record.get("version")):
            return True
    except Exception:
        pass

    checks = (
        "SHOW PROCEDURES EXECUTABLE YIELD name WHERE name = $name RETURN count(*) AS c",
        "SHOW PROCEDURES YIELD name WHERE name = $name RETURN count(*) AS c",
        "CALL dbms.procedures() YIELD name WHERE name = $name RETURN count(*) AS c",
    )
    for cypher in checks:
        try:
            record = session.run(cypher, name=procedure_name).single()
            if record and int(record.get("c", 0) or 0) > 0:
                return True
        except Exception:
            continue
    return False


def has_apoc_add_labels(session: Any) -> bool:
    return _has_procedure(session, "apoc.create.addLabels")


def has_apoc_merge_relationship(session: Any) -> bool:
    return _has_procedure(session, "apoc.merge.relationship")


def has_apoc(session: Any) -> bool:
    return has_apoc_add_labels(session) and has_apoc_merge_relationship(session)


def get_apoc_capabilities(uri: str, user: str, pwd: str) -> tuple[bool, bool]:
    driver = GraphDatabase.driver(uri, auth=(user, pwd))
    try:
        with driver.session() as session:
            return has_apoc_add_labels(session), has_apoc_merge_relationship(session)
    finally:
        driver.close()


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
            apoc = has_apoc_add_labels(session)
            for start in range(0, len(rows), batch_size):
                batch = rows[start: start + batch_size]
                try:
                    if apoc:
                        session.run(UPSERT_NODES, rows=batch)
                    else:
                        session.run(UPSERT_NODES_GENERIC, rows=batch)
                except Exception:
                    session.run(UPSERT_NODES_GENERIC, rows=batch)
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
            rel_cypher = UPSERT_RELS_APOC if has_apoc_merge_relationship(session) else UPSERT_RELS_GENERIC
            for start in range(0, len(rows), batch_size):
                batch = rows[start: start + batch_size]
                try:
                    session.run(rel_cypher, rows=batch)
                except Exception:
                    session.run(UPSERT_RELS_GENERIC, rows=batch)
    finally:
        driver.close()
    return len(rows)
