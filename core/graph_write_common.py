from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from neo4j import GraphDatabase

from core.types import NodePayload, RelationPayload, clean_text

CONSTRAINT_CYPHER = (
    "CREATE CONSTRAINT IF NOT EXISTS "
    "FOR (n:KnowledgeNode) REQUIRE n.uid IS UNIQUE"
)
DEFAULT_VECTOR_INDEX_NAME = "knowledge_node_embedding_idx"

UPSERT_NODES = """
UNWIND $rows AS row
MERGE (n:KnowledgeNode {uid: row.uid})
SET n.name        = row.name,
    n.description = row.description,
    n.group_id    = row.group_id
FOREACH (_ IN CASE WHEN row.embedding IS NULL THEN [] ELSE [1] END |
  SET n.embedding = row.embedding
)
SET n += row.props
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
FOREACH (_ IN CASE WHEN row.embedding IS NULL THEN [] ELSE [1] END |
  SET n.embedding = row.embedding
)
SET n += row.props
"""

UPSERT_RELS_APOC = """
UNWIND $rows AS row
MATCH (src:KnowledgeNode {uid: row.source_uid})
MATCH (tgt:KnowledgeNode {uid: row.target_uid})
CALL apoc.merge.relationship(src, row.rel_type, {group_id: row.group_id}, row.props, tgt)
YIELD rel RETURN count(rel)
"""

UPSERT_RELS_GENERIC = """
UNWIND $rows AS row
MATCH (src:KnowledgeNode {uid: row.source_uid})
MATCH (tgt:KnowledgeNode {uid: row.target_uid})
MERGE (src)-[r:RELATED {rel_type: row.rel_type, group_id: row.group_id}]->(tgt)
SET r += row.props
"""

_PRIMITIVE_TYPES = (str, bool, int, float)
_NODE_RESERVED_PROP_KEYS = {"uid", "name", "description", "group_id", "labels"}
_REL_RESERVED_PROP_KEYS = {"source_uid", "target_uid", "rel_type", "group_id"}


def _is_primitive_list(values: list[Any]) -> bool:
    return all(isinstance(item, _PRIMITIVE_TYPES) for item in values)


def _normalize_leaf_value(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, _PRIMITIVE_TYPES):
        return value.strip() if isinstance(value, str) else value
    if isinstance(value, list):
        if _is_primitive_list(value):
            normalized: list[Any] = []
            for item in value:
                normalized.append(item.strip() if isinstance(item, str) else item)
            return normalized
        return json.dumps(value, ensure_ascii=False, default=str)
    return clean_text(value)


def _flatten_mapping_to_props(
    source: Mapping[str, Any],
    *,
    prefix: str,
    target: dict[str, Any],
    reserved_keys: set[str],
) -> None:
    for key, value in source.items():
        normalized_key = clean_text(key)
        if not normalized_key:
            continue
        flat_key = f"{prefix}_{normalized_key}" if prefix else normalized_key
        if flat_key in reserved_keys:
            continue

        if isinstance(value, Mapping):
            _flatten_mapping_to_props(
                value,
                prefix=flat_key,
                target=target,
                reserved_keys=reserved_keys,
            )
            continue

        normalized_value = _normalize_leaf_value(value)
        if normalized_value == "":
            continue
        target[flat_key] = normalized_value


def _to_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    return {}


def node_payload_to_cypher_row(payload: NodePayload) -> dict[str, Any]:
    props: dict[str, Any] = {}
    _flatten_mapping_to_props(
        _to_mapping(payload.get("properties")),
        prefix="",
        target=props,
        reserved_keys=_NODE_RESERVED_PROP_KEYS,
    )
    _flatten_mapping_to_props(
        _to_mapping(payload.get("meta")),
        prefix="meta",
        target=props,
        reserved_keys=_NODE_RESERVED_PROP_KEYS,
    )
    embedding = payload.get("embedding")
    embedding_value: list[float] | None = None
    if isinstance(embedding, list) and embedding:
        embedding_value = [float(item) for item in embedding if isinstance(item, (int, float))]
        if not embedding_value:
            embedding_value = None

    return {
        "uid": clean_text(payload.get("uid")),
        "name": clean_text(payload.get("name")),
        "description": clean_text(payload.get("description")),
        "group_id": clean_text(payload.get("group_id")),
        "labels": payload.get("labels") or ["Node"],
        "embedding": embedding_value,
        "props": props,
    }


def relation_payload_to_cypher_row(payload: RelationPayload) -> dict[str, Any]:
    props: dict[str, Any] = {}
    _flatten_mapping_to_props(
        _to_mapping(payload.get("properties")),
        prefix="",
        target=props,
        reserved_keys=_REL_RESERVED_PROP_KEYS,
    )
    _flatten_mapping_to_props(
        _to_mapping(payload.get("meta")),
        prefix="meta",
        target=props,
        reserved_keys=_REL_RESERVED_PROP_KEYS,
    )
    direction = clean_text(payload.get("direction"))
    if direction:
        props["direction"] = direction
    description = clean_text(payload.get("description"))
    if description:
        props["description"] = description
    weight = payload.get("weight")
    if isinstance(weight, (int, float)):
        props["weight"] = float(weight)

    return {
        "source_uid": clean_text(payload.get("source_uid")),
        "target_uid": clean_text(payload.get("target_uid")),
        "rel_type": clean_text(payload.get("rel_type")),
        "group_id": clean_text(payload.get("group_id")),
        "props": props,
    }


def get_credentials(runtime: Any) -> tuple[str, str, str]:
    uri = str(runtime.credentials.get("neo4j_uri", "")).strip()
    user = str(runtime.credentials.get("neo4j_user", "")).strip()
    pwd = str(runtime.credentials.get("neo4j_password", "")).strip()
    return uri, user, pwd


def clear_graph(uri: str, user: str, pwd: str, group_id: str = "") -> None:
    """清理图数据：传 group_id 时按组清理，否则清空全图。"""
    driver = GraphDatabase.driver(uri, auth=(user, pwd))
    try:
        with driver.session() as session:
            normalized_group_id = clean_text(group_id)
            if normalized_group_id:
                # 先删该组关系，再删该组节点，避免跨组节点被误删。
                session.run(
                    """
                    MATCH ()-[r]-()
                    WHERE r.group_id = $group_id
                    DELETE r
                    """,
                    group_id=normalized_group_id,
                )
                session.run(
                    """
                    MATCH (n:KnowledgeNode)
                    WHERE n.group_id = $group_id
                    DETACH DELETE n
                    """,
                    group_id=normalized_group_id,
                )
            else:
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
    cypher_rows = [node_payload_to_cypher_row(row) for row in rows]
    detected_dimensions = _detect_embedding_dimensions(cypher_rows)
    driver = GraphDatabase.driver(uri, auth=(user, pwd))
    try:
        with driver.session() as session:
            session.run(CONSTRAINT_CYPHER)
            if detected_dimensions > 0:
                _ensure_vector_index(
                    session,
                    index_name=DEFAULT_VECTOR_INDEX_NAME,
                    dimensions=detected_dimensions,
                )
            apoc = has_apoc_add_labels(session)
            for start in range(0, len(cypher_rows), batch_size):
                batch = cypher_rows[start: start + batch_size]
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


def _detect_embedding_dimensions(rows: list[dict[str, Any]]) -> int:
    for row in rows:
        embedding = row.get("embedding")
        if isinstance(embedding, list) and embedding:
            return len(embedding)
    return 0


def _ensure_vector_index(
    session: Any,
    *,
    index_name: str,
    dimensions: int,
) -> None:
    if dimensions <= 0:
        return

    query = f"""
CREATE VECTOR INDEX `{index_name}` IF NOT EXISTS
FOR (n:KnowledgeNode) ON (n.embedding)
OPTIONS {{indexConfig: {{
  `vector.dimensions`: {dimensions},
  `vector.similarity_function`: 'cosine'
}}}}
"""
    try:
        session.run(query)
    except Exception:
        # 向量索引创建失败不应阻断节点写入；查询阶段会自动走文本回退
        return


def write_relations(
    uri: str,
    user: str,
    pwd: str,
    rows: list[RelationPayload],
    *,
    batch_size: int,
) -> int:
    cypher_rows = [relation_payload_to_cypher_row(row) for row in rows]
    driver = GraphDatabase.driver(uri, auth=(user, pwd))
    try:
        with driver.session() as session:
            rel_cypher = UPSERT_RELS_APOC if has_apoc_merge_relationship(session) else UPSERT_RELS_GENERIC
            for start in range(0, len(cypher_rows), batch_size):
                batch = cypher_rows[start: start + batch_size]
                try:
                    session.run(rel_cypher, rows=batch)
                except Exception:
                    session.run(UPSERT_RELS_GENERIC, rows=batch)
    finally:
        driver.close()
    return len(rows)
