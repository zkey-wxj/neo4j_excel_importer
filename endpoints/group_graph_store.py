from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from neo4j import GraphDatabase


class GroupGraphStore:
    """封装 group 图谱的 Neo4j 读写逻辑。"""

    _NODES_QUERY = """
MATCH (n:KnowledgeNode)
WHERE coalesce(n.group_id, '') = $group_id
RETURN n
ORDER BY coalesce(n.name, n.uid) ASC
LIMIT $limit
"""

    _RELS_QUERY = """
MATCH (src:KnowledgeNode)-[r:RELATED]->(tgt:KnowledgeNode)
WHERE coalesce(r.group_id, '') = $group_id
   OR (coalesce(src.group_id, '') = $group_id AND coalesce(tgt.group_id, '') = $group_id)
RETURN src, r, tgt
ORDER BY coalesce(src.uid, ''), coalesce(tgt.uid, ''), coalesce(r.rel_type, '')
LIMIT $limit
"""

    _UPSERT_NODE = """
MERGE (n:KnowledgeNode {uid: $uid, group_id: $group_id})
SET n.name = $name,
    n.description = $description,
    n.properties = $properties,
    n.labels = $labels
RETURN elementId(n) AS node_id
"""

    _DELETE_NODE = """
MATCH (n:KnowledgeNode {uid: $uid, group_id: $group_id})
DETACH DELETE n
RETURN count(*) AS deleted
"""

    _CREATE_REL = """
MATCH (src:KnowledgeNode {uid: $source_uid, group_id: $group_id})
MATCH (tgt:KnowledgeNode {uid: $target_uid, group_id: $group_id})
CREATE (src)-[r:RELATED {
  rel_type: $rel_type,
  group_id: $group_id,
  description: $description,
  properties: $properties
}]->(tgt)
RETURN elementId(r) AS relation_id
"""

    _UPDATE_REL_BY_ID = """
MATCH ()-[r:RELATED]->()
WHERE elementId(r) = $relation_id
SET r.rel_type = $rel_type,
    r.description = $description,
    r.properties = $properties
RETURN elementId(r) AS relation_id
"""

    _DELETE_REL_BY_ID = """
MATCH ()-[r:RELATED]->()
WHERE elementId(r) = $relation_id
DELETE r
RETURN count(*) AS deleted
"""

    def __init__(self, settings: Mapping[str, Any]):
        """初始化连接配置。"""
        self.uri = self._clean(settings.get("neo4j_uri"))
        self.user = self._clean(settings.get("neo4j_user"))
        self.password = self._clean(settings.get("neo4j_password"))
        self.database = self._clean(settings.get("neo4j_database"))
        if not self.uri or not self.user or not self.password:
            raise ValueError("Endpoint settings 缺少 neo4j_uri/neo4j_user/neo4j_password")

    def query_graph(self, group_id: str, limit: int) -> dict[str, Any]:
        """查询 group_id 下的图谱节点与关系。"""
        node_rows = self._run(self._NODES_QUERY, {"group_id": group_id, "limit": limit})
        rel_rows = self._run(self._RELS_QUERY, {"group_id": group_id, "limit": limit})

        nodes: dict[str, dict[str, Any]] = {}
        rels: list[dict[str, Any]] = []

        for row in node_rows:
            node = self._serialize_node(row.get("n"))
            if node:
                nodes[node["uid"]] = node

        for row in rel_rows:
            src = self._serialize_node(row.get("src"))
            tgt = self._serialize_node(row.get("tgt"))
            if src:
                nodes[src["uid"]] = src
            if tgt:
                nodes[tgt["uid"]] = tgt
            rel = self._serialize_relation(row.get("r"), src, tgt)
            if rel:
                rels.append(rel)

        return {
            "group_id": group_id,
            "nodes": list(nodes.values()),
            "relations": rels,
            "nodes_count": len(nodes),
            "relations_count": len(rels),
            "limit_applied": limit,
        }

    def create_node(self, payload: Mapping[str, Any]) -> str:
        """幂等写入节点并返回 element id。"""
        params = self._node_params(payload)
        rows = self._run(self._UPSERT_NODE, params, write=True)
        return self._clean((rows[0] if rows else {}).get("node_id"))

    def update_node(self, payload: Mapping[str, Any]) -> str:
        """更新节点并返回 element id。"""
        params = self._node_params(payload)
        rows = self._run(self._UPSERT_NODE, params, write=True)
        return self._clean((rows[0] if rows else {}).get("node_id"))

    def delete_node(self, payload: Mapping[str, Any]) -> int:
        """按 uid + group_id 删除节点。"""
        params = {
            "group_id": self._clean(payload.get("group_id")),
            "uid": self._clean(payload.get("uid")),
        }
        rows = self._run(self._DELETE_NODE, params, write=True)
        return int((rows[0] if rows else {}).get("deleted", 0) or 0)

    def create_relation(self, payload: Mapping[str, Any]) -> str:
        """新增关系并返回 element id。"""
        params = self._relation_create_params(payload)
        rows = self._run(self._CREATE_REL, params, write=True)
        return self._clean((rows[0] if rows else {}).get("relation_id"))

    def update_relation(self, payload: Mapping[str, Any]) -> str:
        """通过 relation_id 更新关系并返回 element id。"""
        params = self._relation_update_params(payload)
        rows = self._run(self._UPDATE_REL_BY_ID, params, write=True)
        return self._clean((rows[0] if rows else {}).get("relation_id"))

    def delete_relation(self, payload: Mapping[str, Any]) -> int:
        """通过 relation_id 删除关系。"""
        relation_id = self._clean(payload.get("relation_id"))
        rows = self._run(self._DELETE_REL_BY_ID, {"relation_id": relation_id}, write=True)
        return int((rows[0] if rows else {}).get("deleted", 0) or 0)

    def _node_params(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "group_id": self._clean(payload.get("group_id")),
            "uid": self._clean(payload.get("uid")),
            "name": self._clean(payload.get("name")) or self._clean(payload.get("uid")),
            "description": self._clean(payload.get("description")),
            "labels": self._str_list(payload.get("labels")) or ["Node"],
            "properties": payload.get("properties") if isinstance(payload.get("properties"), dict) else {},
        }

    def _relation_create_params(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "group_id": self._clean(payload.get("group_id")),
            "source_uid": self._clean(payload.get("source_uid")),
            "target_uid": self._clean(payload.get("target_uid")),
            "rel_type": self._clean(payload.get("rel_type")),
            "description": self._clean(payload.get("description")),
            "properties": payload.get("properties") if isinstance(payload.get("properties"), dict) else {},
        }

    def _relation_update_params(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "relation_id": self._clean(payload.get("relation_id")),
            "rel_type": self._clean(payload.get("rel_type")) or "RELATED",
            "description": self._clean(payload.get("description")),
            "properties": payload.get("properties") if isinstance(payload.get("properties"), dict) else {},
        }

    def _run(self, query: str, parameters: dict[str, Any], write: bool = False) -> list[dict[str, Any]]:
        driver = GraphDatabase.driver(self.uri, auth=(self.user, self.password), connection_timeout=30.0)
        try:
            kwargs: dict[str, Any] = {}
            if self.database:
                kwargs["database"] = self.database
            with driver.session(**kwargs) as session:
                result = session.run(query, parameters)
                rows = [record.data() for record in result]
                if write:
                    result.consume()
                return rows
        finally:
            driver.close()

    def _serialize_node(self, value: Any) -> dict[str, Any] | None:
        if value is None:
            return None
        data = self._mapping(value)
        uid = self._clean(data.get("uid"))
        if not uid:
            return None
        labels = self._str_list(data.get("labels"))
        return {
            "uid": uid,
            "name": self._clean(data.get("name")) or uid,
            "group_id": self._clean(data.get("group_id")),
            "description": self._clean(data.get("description")),
            "labels": labels,
            "properties": data.get("properties") if isinstance(data.get("properties"), dict) else {},
        }

    def _serialize_relation(
        self,
        rel_obj: Any,
        src_node: dict[str, Any] | None,
        tgt_node: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if rel_obj is None or src_node is None or tgt_node is None:
            return None
        data = self._mapping(rel_obj)
        rel_type = self._clean(data.get("rel_type"))
        if not rel_type and hasattr(rel_obj, "type"):
            rel_type = self._clean(getattr(rel_obj, "type"))
        rel_id = self._clean(getattr(rel_obj, "element_id", ""))
        return {
            "id": rel_id,
            "source_uid": src_node.get("uid"),
            "target_uid": tgt_node.get("uid"),
            "rel_type": rel_type or "RELATED",
            "group_id": self._clean(data.get("group_id")),
            "description": self._clean(data.get("description")),
            "properties": data.get("properties") if isinstance(data.get("properties"), dict) else {},
        }

    def _mapping(self, value: Any) -> Mapping[str, Any]:
        if isinstance(value, Mapping):
            return value
        if hasattr(value, "items"):
            try:
                return dict(value.items())
            except Exception:
                return {}
        return {}

    def _str_list(self, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        result: list[str] = []
        for item in value:
            text = self._clean(item)
            if text:
                result.append(text)
        return result

    def _clean(self, value: Any) -> str:
        return str(value or "").strip()
