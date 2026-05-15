from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from neo4j import GraphDatabase

from core.graph_write_common import node_payload_to_cypher_row, relation_payload_to_cypher_row
from core.types import NODE_PAYLOAD_FIELDS, RELATION_PAYLOAD_FIELDS


class GroupGraphStore:
    """封装 group 图谱的 Neo4j 读写逻辑。"""

    _NODE_RESERVED_PROP_KEYS = set(NODE_PAYLOAD_FIELDS)
    _REL_RESERVED_PROP_KEYS = set(RELATION_PAYLOAD_FIELDS)

    _COUNT_NODES_QUERY = """
MATCH (n:KnowledgeNode)
WHERE coalesce(n.group_id, '') = $group_id
RETURN count(n) AS total
"""

    _COUNT_RELS_QUERY = """
MATCH (src:KnowledgeNode)-[r]->(tgt:KnowledgeNode)
WHERE coalesce(r.group_id, '') = $group_id
   OR (coalesce(src.group_id, '') = $group_id AND coalesce(tgt.group_id, '') = $group_id)
RETURN count(r) AS total
"""

    _NODES_QUERY = """
MATCH (n:KnowledgeNode)
WHERE coalesce(n.group_id, '') = $group_id
RETURN n
ORDER BY coalesce(n.name, n.uid) ASC
SKIP $offset
LIMIT $limit
"""

    _RELS_QUERY = """
MATCH (src:KnowledgeNode)-[r]->(tgt:KnowledgeNode)
WHERE coalesce(r.group_id, '') = $group_id
   OR (coalesce(src.group_id, '') = $group_id AND coalesce(tgt.group_id, '') = $group_id)
RETURN src, r, tgt
ORDER BY coalesce(src.uid, ''), coalesce(tgt.uid, ''), coalesce(r.rel_type, type(r), '')
SKIP $offset
LIMIT $limit
"""

    _UPSERT_NODE = """
MERGE (n:KnowledgeNode {uid: $uid, group_id: $group_id})
SET n.name = $name,
    n.description = $description,
    n.labels = $labels
REMOVE n.properties, n.meta
SET n += $props
FOREACH (_ IN CASE WHEN $embedding IS NULL THEN [] ELSE [1] END |
  SET n.embedding = $embedding
)
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
  group_id: $group_id
}]->(tgt)
SET r += $props
RETURN elementId(r) AS relation_id
"""

    _UPDATE_REL_BY_ENDPOINTS = """
MATCH (src:KnowledgeNode {uid: $source_uid, group_id: $group_id})-[r:RELATED]->(tgt:KnowledgeNode {uid: $target_uid, group_id: $group_id})
SET r.rel_type = $rel_type,
    r.group_id = $group_id,
    r.description = $description
REMOVE r.properties, r.meta
SET r += $props
RETURN elementId(r) AS relation_id
"""

    _DELETE_REL_BY_ENDPOINTS = """
MATCH (src:KnowledgeNode {uid: $source_uid, group_id: $group_id})-[r:RELATED]->(tgt:KnowledgeNode {uid: $target_uid, group_id: $group_id})
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
        self._driver = None
        self._ensure_driver()

    def _ensure_driver(self) -> None:
        if self._driver is None:
            self._driver = GraphDatabase.driver(
                self.uri,
                auth=(self.user, self.password),
                connection_timeout=30.0,
            )

    def query_graph(self, group_id: str, page: int, page_size: int) -> dict[str, Any]:
        """分页查询 group_id 下的图谱节点与关系。"""
        self._ensure_driver()
        offset = max(0, (page - 1) * page_size)
        limit = page_size + 1
        nodes_total = -1
        relations_total = -1
        kwargs: dict[str, Any] = {}
        if self.database:
            kwargs["database"] = self.database
        with self._driver.session(**kwargs) as session:
            # 只在第一页查询 total，避免每次分页都全量 count。
            if page == 1:
                node_count_rows = [record.data() for record in session.run(self._COUNT_NODES_QUERY, {"group_id": group_id})]
                rel_count_rows = [record.data() for record in session.run(self._COUNT_RELS_QUERY, {"group_id": group_id})]
                nodes_total = int((node_count_rows[0] if node_count_rows else {}).get("total", 0) or 0)
                relations_total = int((rel_count_rows[0] if rel_count_rows else {}).get("total", 0) or 0)

            node_rows = [record.data() for record in session.run(
                self._NODES_QUERY,
                {"group_id": group_id, "offset": offset, "limit": limit},
            )]
            rel_rows = [record.data() for record in session.run(
                self._RELS_QUERY,
                {"group_id": group_id, "offset": offset, "limit": limit},
            )]

        nodes_has_more = len(node_rows) > page_size
        relations_has_more = len(rel_rows) > page_size
        if nodes_has_more:
            node_rows = node_rows[:page_size]
        if relations_has_more:
            rel_rows = rel_rows[:page_size]

        nodes: dict[str, dict[str, Any]] = {}
        rels: list[dict[str, Any]] = []

        for row in node_rows:
            node = self._serialize_node(row.get("n"))
            if node:
                nodes[node["uid"]] = node

        for row in rel_rows:
            src = self._serialize_node(row.get("src"))
            tgt = self._serialize_node(row.get("tgt"))
            rel = self._serialize_relation(row.get("r"), src, tgt)
            if rel:
                rels.append(rel)

        return {
            "group_id": group_id,
            "page": page,
            "page_size": page_size,
            "offset": offset,
            "nodes": list(nodes.values()),
            "relations": rels,
            "nodes_total": nodes_total,
            "relations_total": relations_total,
            "nodes_count": len(nodes),
            "relations_count": len(rels),
            "nodes_has_more": nodes_has_more,
            "relations_has_more": relations_has_more,
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
        if rows:
            return f"{params['source_uid']}->{params['target_uid']}"
        return ""

    def update_relation(self, payload: Mapping[str, Any]) -> str:
        """通过 group_id + source_uid + target_uid 更新关系并返回引用键。"""
        params = self._relation_update_params(payload)
        rows = self._run(self._UPDATE_REL_BY_ENDPOINTS, params, write=True)
        if rows:
            return f"{params['source_uid']}->{params['target_uid']}"
        return ""

    def delete_relation(self, payload: Mapping[str, Any]) -> int:
        """通过 group_id + source_uid + target_uid 删除关系。"""
        group_id = self._clean(payload.get("group_id"))
        source_uid = self._clean(payload.get("source_uid"))
        target_uid = self._clean(payload.get("target_uid"))
        rows = self._run(
            self._DELETE_REL_BY_ENDPOINTS,
            {"group_id": group_id, "source_uid": source_uid, "target_uid": target_uid},
            write=True,
        )
        return int((rows[0] if rows else {}).get("deleted", 0) or 0)

    def _node_params(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        row = node_payload_to_cypher_row(
            {
                "uid": self._clean(payload.get("uid")),
                "name": self._clean(payload.get("name")) or self._clean(payload.get("uid")),
                "labels": self._str_list(payload.get("labels")) or ["Node"],
                "description": self._clean(payload.get("description")),
                "group_id": self._clean(payload.get("group_id")),
                "properties": payload.get("properties") if isinstance(payload.get("properties"), Mapping) else {},
                "meta": payload.get("meta") if isinstance(payload.get("meta"), Mapping) else {},
            }
        )
        return {
            "group_id": row["group_id"],
            "uid": row["uid"],
            "name": row["name"],
            "description": row["description"],
            "labels": row["labels"],
            "props": row["props"],
            "embedding": row["embedding"],
        }

    def _relation_create_params(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        row = relation_payload_to_cypher_row(
            {
                "source_uid": self._clean(payload.get("source_uid")),
                "target_uid": self._clean(payload.get("target_uid")),
                "rel_type": self._clean(payload.get("rel_type")) or "RELATED",
                "direction": "forward",
                "group_id": self._clean(payload.get("group_id")),
                "description": self._clean(payload.get("description")),
                "properties": payload.get("properties") if isinstance(payload.get("properties"), Mapping) else {},
                "meta": payload.get("meta") if isinstance(payload.get("meta"), Mapping) else {},
            }
        )
        return {
            "source_uid": row["source_uid"],
            "target_uid": row["target_uid"],
            "rel_type": row["rel_type"],
            "group_id": row["group_id"],
            "props": row["props"],
        }

    def _relation_update_params(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        group_id = self._clean(payload.get("group_id"))
        source_uid = self._clean(payload.get("source_uid"))
        target_uid = self._clean(payload.get("target_uid"))
        rel_type = self._clean(payload.get("rel_type")) or "RELATED"
        if not group_id:
            raise ValueError("group_id 不能为空")
        if not source_uid:
            raise ValueError("source_uid 不能为空")
        if not target_uid:
            raise ValueError("target_uid 不能为空")

        input_props = payload.get("properties")
        input_meta = payload.get("meta")
        description = self._clean(payload.get("description"))
        direction = self._clean(payload.get("direction")) or "forward"
        weight = payload.get("weight")
        if not isinstance(weight, (int, float)):
            weight = None

        row = relation_payload_to_cypher_row(
            {
                "source_uid": source_uid,
                "target_uid": target_uid,
                "rel_type": rel_type,
                "direction": direction,
                "group_id": group_id,
                "description": description,
                "weight": weight,
                "properties": input_props if isinstance(input_props, Mapping) else {},
                "meta": input_meta if isinstance(input_meta, Mapping) else {},
            }
        )
        return {
            "source_uid": source_uid,
            "target_uid": target_uid,
            "rel_type": row["rel_type"],
            "group_id": row["group_id"],
            "description": row["props"].get("description", ""),
            "props": row["props"],
        }

    def _run(self, query: str, parameters: dict[str, Any], write: bool = False) -> list[dict[str, Any]]:
        self._ensure_driver()
        kwargs: dict[str, Any] = {}
        if self.database:
            kwargs["database"] = self.database
        with self._driver.session(**kwargs) as session:
            result = session.run(query, parameters)
            rows = [record.data() for record in result]
            if write:
                result.consume()
            return rows

    def close(self) -> None:
        """关闭 Neo4j driver。"""
        if self._driver is not None:
            self._driver.close()
            self._driver = None

    def _serialize_node(self, value: Any) -> dict[str, Any] | None:
        if value is None:
            return None
        data = self._mapping(value)
        uid = self._clean(data.get("uid"))
        if not uid:
            return None
        labels = self._str_list(data.get("labels"))
        properties = self._extract_node_properties(data)
        meta = self._extract_node_meta(properties)
        return {
            "uid": uid,
            "name": self._clean(data.get("name")) or uid,
            "group_id": self._clean(data.get("group_id")),
            "description": self._clean(data.get("description")),
            "labels": labels,
            "properties": properties,
            "meta": meta,
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
            "properties": self._extract_relation_properties(data),
        }

    def _extract_node_properties(self, node_map: Mapping[str, Any]) -> dict[str, Any]:
        props: dict[str, Any] = {}

        legacy_properties = node_map.get("properties")
        if isinstance(legacy_properties, Mapping):
            for key, value in legacy_properties.items():
                normalized_key = self._clean(key)
                if normalized_key and value not in (None, ""):
                    props[normalized_key] = value

        for key, value in node_map.items():
            normalized_key = self._clean(key)
            if not normalized_key or normalized_key in self._NODE_RESERVED_PROP_KEYS:
                continue
            if value in (None, ""):
                continue
            props[normalized_key] = value

        return props

    def _extract_node_meta(self, props: dict[str, Any]) -> dict[str, Any]:
        meta: dict[str, Any] = {}
        for key in list(props.keys()):
            normalized_key = self._clean(key)
            if not normalized_key.startswith("meta_"):
                continue
            meta_key = self._clean(normalized_key[5:])
            value = props.pop(key, None)
            if not meta_key or value in (None, ""):
                continue
            meta[meta_key] = value
        return meta

    def _extract_relation_properties(self, rel_map: Mapping[str, Any]) -> dict[str, Any]:
        props: dict[str, Any] = {}

        legacy_properties = rel_map.get("properties")
        if isinstance(legacy_properties, Mapping):
            for key, value in legacy_properties.items():
                normalized_key = self._clean(key)
                if normalized_key and value not in (None, ""):
                    props[normalized_key] = value

        for key, value in rel_map.items():
            normalized_key = self._clean(key)
            if not normalized_key or normalized_key in self._REL_RESERVED_PROP_KEYS:
                continue
            if value in (None, ""):
                continue
            props[normalized_key] = value

        return props

    def _split_meta_from_props(self, props: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        meta: dict[str, Any] = {}
        clean_props = dict(props)
        for key in list(clean_props.keys()):
            normalized_key = self._clean(key)
            if not normalized_key.startswith("meta_"):
                continue
            meta_key = self._clean(normalized_key[5:])
            value = clean_props.pop(key, None)
            if meta_key and value not in (None, ""):
                meta[meta_key] = value
        return meta, clean_props

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
