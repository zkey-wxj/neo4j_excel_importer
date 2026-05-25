from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from typing import Any, cast

from neo4j import Driver, GraphDatabase

from core.constants import DEFAULT_NODE_LABEL, DEFAULT_REL_TYPE, NODE_RESERVED_PROP_KEYS, RELATION_RESERVED_PROP_KEYS
from core.graph_query_common import _ensure_group_id_index, _ensure_group_id_prop
from core.graph_write_common import node_payload_to_cypher_row, relation_payload_to_cypher_row
from core.types import NodePayload, RelationPayload, clean_text, extract_properties, split_meta_from_props


class GroupGraphStore:
    """封装 group 图谱的 Neo4j 读写逻辑。"""

    _log = logging.getLogger("GroupGraphStore")

    _COUNT_QUERY = """
CALL {
  MATCH (n:KnowledgeNode {group_id: $group_id})
  RETURN count(n) AS node_count
}
CALL {
  MATCH (src:KnowledgeNode {group_id: $group_id})-[r]->(tgt:KnowledgeNode)
  WHERE r.group_id = $group_id OR tgt.group_id = $group_id
  RETURN count(r) AS rel_count
}
RETURN node_count, rel_count
"""

    _NODES_QUERY = """
MATCH (n:KnowledgeNode {group_id: $group_id})
RETURN n, labels(n) AS neo_labels
SKIP $offset
LIMIT $limit
"""

    _RELS_QUERY = """
MATCH (src:KnowledgeNode {group_id: $group_id})-[r]->(tgt:KnowledgeNode)
WHERE r.group_id = $group_id OR tgt.group_id = $group_id
RETURN src, labels(src) AS src_labels, r, type(r) AS r_type, elementId(r) AS r_id, tgt, labels(tgt) AS tgt_labels
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

    _MERGE_REL = """
MATCH (src:KnowledgeNode {uid: $source_uid, group_id: $group_id})
MATCH (tgt:KnowledgeNode {uid: $target_uid, group_id: $group_id})
MERGE (src)-[r:RELATED]->(tgt)
SET r.rel_type = $rel_type,
    r.group_id = $group_id
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

    _REDIRECT_OUTGOING_RELS = """
MATCH (old:KnowledgeNode {uid: $old_uid, group_id: $group_id})-[r:RELATED]->(tgt:KnowledgeNode)
WHERE tgt.uid <> $new_uid OR tgt.group_id <> $group_id
WITH old, tgt, r, properties(r) AS rProps
DELETE r
WITH old, tgt, rProps
MERGE (new:KnowledgeNode {uid: $new_uid, group_id: $group_id})
CREATE (new)-[nr:RELATED]->(tgt)
SET nr = rProps
RETURN count(*) AS redirected
"""

    _REDIRECT_INCOMING_RELS = """
MATCH (src:KnowledgeNode)-[r:RELATED]->(old:KnowledgeNode {uid: $old_uid, group_id: $group_id})
WHERE src.uid <> $new_uid OR src.group_id <> $group_id
WITH src, old, r, properties(r) AS rProps
DELETE r
WITH src, old, rProps
MERGE (new:KnowledgeNode {uid: $new_uid, group_id: $group_id})
CREATE (src)-[nr:RELATED]->(new)
SET nr = rProps
RETURN count(*) AS redirected
"""

    def __init__(self, settings: Mapping[str, Any]):
        """初始化连接配置。"""
        self.uri = clean_text(settings.get("neo4j_uri"))
        self.user = clean_text(settings.get("neo4j_user"))
        self.password = clean_text(settings.get("neo4j_password"))
        self.database = clean_text(settings.get("neo4j_database"))
        if not self.uri or not self.user or not self.password:
            raise ValueError("Endpoint settings 缺少 neo4j_uri/neo4j_user/neo4j_password")
        self._driver: Driver | None = None
        self._ensure_driver()

    def _ensure_driver(self) -> None:
        if self._driver is None:
            self._driver = GraphDatabase.driver(
                self.uri,
                auth=(self.user, self.password),
                connection_timeout=30.0,
            )
            kwargs: dict[str, Any] = {}
            if self.database:
                kwargs["database"] = self.database
            with self._driver.session(**kwargs) as session:
                _ensure_group_id_index(session, self.database)
                _ensure_group_id_prop(session, self.database)

    def query_graph(self, group_id: str, page: int, page_size: int) -> dict[str, Any]:
        """分页查询 group_id 下的图谱节点与关系。"""
        self._ensure_driver()
        offset = max(0, (page - 1) * page_size)
        limit = page_size + 1
        nodes_total = -1
        relations_total = -1
        db_timings: dict[str, Any] = {}
        kwargs: dict[str, Any] = {}
        if self.database:
            kwargs["database"] = self.database
        assert self._driver is not None
        with self._driver.session(**kwargs) as session:
            if page == 1:
                t0 = time.perf_counter()
                count_rows = [record.data() for record in session.run(self._COUNT_QUERY, {"group_id": group_id})]
                db_timings["count"] = round((time.perf_counter() - t0) * 1000, 1)
                row = count_rows[0] if count_rows else {}
                nodes_total = int(row.get("node_count", 0) or 0)
                relations_total = int(row.get("rel_count", 0) or 0)
                db_timings["count_raw"] = row

            params = {"group_id": group_id, "offset": offset, "limit": limit}

            t0 = time.perf_counter()
            node_rows = [record.data() for record in session.run(self._NODES_QUERY, params)]
            db_timings["data_nodes"] = round((time.perf_counter() - t0) * 1000, 1)

            t0 = time.perf_counter()
            rel_rows = [record.data() for record in session.run(self._RELS_QUERY, params)]
            db_timings["data_rels"] = round((time.perf_counter() - t0) * 1000, 1)

        nodes_has_more = len(node_rows) > page_size
        relations_has_more = len(rel_rows) > page_size
        if nodes_has_more:
            node_rows = node_rows[:page_size]
        if relations_has_more:
            rel_rows = rel_rows[:page_size]

        nodes: dict[str, dict[str, Any]] = {}
        rels: list[dict[str, Any]] = []

        for row in node_rows:
            node = self._serialize_node(row.get("n"), explicit_labels=row.get("neo_labels"))
            if node:
                nodes[node["uid"]] = node

        for row in rel_rows:
            src = self._serialize_node(row.get("src"), explicit_labels=row.get("src_labels"))
            tgt = self._serialize_node(row.get("tgt"), explicit_labels=row.get("tgt_labels"))
            if src:
                nodes[src["uid"]] = src
            if tgt:
                nodes[tgt["uid"]] = tgt
            rel = self._serialize_relation(row.get("r"), src, tgt, explicit_type=row.get("r_type"), explicit_id=row.get("r_id"))
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
            "db_timings": db_timings,
        }

    def create_node(self, payload: Mapping[str, Any]) -> str:
        """幂等写入节点并返回 element id。"""
        params = self._node_params(payload)
        rows = self._run(self._UPSERT_NODE, params, write=True)
        return clean_text((rows[0] if rows else {}).get("node_id"))

    def update_node(self, payload: Mapping[str, Any]) -> str:
        """更新节点并返回 element id。"""
        params = self._node_params(payload)
        rows = self._run(self._UPSERT_NODE, params, write=True)
        return clean_text((rows[0] if rows else {}).get("node_id"))

    def delete_node(self, payload: Mapping[str, Any]) -> int:
        """按 uid + group_id 删除节点。"""
        params = {
            "group_id": clean_text(payload.get("group_id")),
            "uid": clean_text(payload.get("uid")),
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
        group_id = clean_text(payload.get("group_id"))
        source_uid = clean_text(payload.get("source_uid"))
        target_uid = clean_text(payload.get("target_uid"))
        rows = self._run(
            self._DELETE_REL_BY_ENDPOINTS,
            {"group_id": group_id, "source_uid": source_uid, "target_uid": target_uid},
            write=True,
        )
        return int((rows[0] if rows else {}).get("deleted", 0) or 0)

    def replace_node_relations(self, group_id: str, old_uid: str, new_uid: str) -> int:
        """将 old_uid 节点的全部关系转移至 new_uid 节点，返回转移的关系数。"""
        if old_uid == new_uid:
            return 0
        params = {"group_id": group_id, "old_uid": old_uid, "new_uid": new_uid}
        out_rows = self._run(self._REDIRECT_OUTGOING_RELS, params, write=True)
        in_rows = self._run(self._REDIRECT_INCOMING_RELS, params, write=True)
        out_count = int((out_rows[0] if out_rows else {}).get("redirected", 0) or 0)
        in_count = int((in_rows[0] if in_rows else {}).get("redirected", 0) or 0)
        return out_count + in_count

    def _node_params(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        row = node_payload_to_cypher_row(
            cast(NodePayload, {
                "uid": clean_text(payload.get("uid")),
                "name": clean_text(payload.get("name")) or clean_text(payload.get("uid")),
                "labels": self._str_list(payload.get("labels")) or ["Node"],
                "description": clean_text(payload.get("description")),
                "group_id": clean_text(payload.get("group_id")),
                "properties": payload.get("properties") if isinstance(payload.get("properties"), Mapping) else {},
                "meta": payload.get("meta") if isinstance(payload.get("meta"), Mapping) else {},
            })
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
            cast(RelationPayload, {
                "source_uid": clean_text(payload.get("source_uid")),
                "target_uid": clean_text(payload.get("target_uid")),
                "rel_type": clean_text(payload.get("rel_type")) or "RELATED",
                "direction": "forward",
                "group_id": clean_text(payload.get("group_id")),
                "description": clean_text(payload.get("description")),
                "properties": payload.get("properties") if isinstance(payload.get("properties"), Mapping) else {},
                "meta": payload.get("meta") if isinstance(payload.get("meta"), Mapping) else {},
            })
        )
        return {
            "source_uid": row["source_uid"],
            "target_uid": row["target_uid"],
            "rel_type": row["rel_type"],
            "group_id": row["group_id"],
            "props": row["props"],
        }

    def _relation_update_params(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        group_id = clean_text(payload.get("group_id"))
        source_uid = clean_text(payload.get("source_uid"))
        target_uid = clean_text(payload.get("target_uid"))
        rel_type = clean_text(payload.get("rel_type")) or "RELATED"
        if not group_id:
            raise ValueError("group_id 不能为空")
        if not source_uid:
            raise ValueError("source_uid 不能为空")
        if not target_uid:
            raise ValueError("target_uid 不能为空")

        input_props = payload.get("properties")
        input_meta = payload.get("meta")
        description = clean_text(payload.get("description"))
        direction = clean_text(payload.get("direction")) or "forward"
        weight = payload.get("weight")
        if not isinstance(weight, (int, float)):
            weight = None

        row = relation_payload_to_cypher_row(
            cast(RelationPayload, {
                "source_uid": source_uid,
                "target_uid": target_uid,
                "rel_type": rel_type,
                "direction": direction,
                "group_id": group_id,
                "description": description,
                "weight": weight,
                "properties": input_props if isinstance(input_props, Mapping) else {},
                "meta": input_meta if isinstance(input_meta, Mapping) else {},
            })
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
        assert self._driver is not None
        with self._driver.session(**kwargs) as session:
            result = session.run(query, parameters)  # type: ignore[arg-type]
            rows = [record.data() for record in result]
            if write:
                result.consume()
            return rows

    _NEIGHBORS_QUERY_TEMPLATE = """
MATCH (start:KnowledgeNode {{uid: $uid, group_id: $group_id}})
CALL {{
  WITH start
  MATCH path = (start)-[*1..{depth}]->(n:KnowledgeNode)
  WHERE n.group_id = $group_id
  RETURN nodes(path) AS path_nodes, relationships(path) AS path_rels
  UNION
  WITH start
  MATCH path = (n:KnowledgeNode)-[*1..{depth}]->(start)
  WHERE n.group_id = $group_id
  RETURN nodes(path) AS path_nodes, relationships(path) AS path_rels
}}
UNWIND path_nodes AS pn
UNWIND path_rels AS pr
RETURN collect(DISTINCT pn) AS nodes, collect(DISTINCT pr) AS rels
"""

    _PATH_QUERY = """
MATCH (src:KnowledgeNode {uid: $source_uid, group_id: $group_id}),
      (tgt:KnowledgeNode {uid: $target_uid, group_id: $group_id})
MATCH path = shortestPath((src)-[*..15]-(tgt))
RETURN [n IN nodes(path) | n] AS nodes,
       [r IN relationships(path) | r] AS rels,
       length(path) AS path_length
"""

    _STATS_QUERY = """
CALL {
  MATCH (n:KnowledgeNode {group_id: $group_id})
  RETURN collect(DISTINCT n) AS all_nodes, count(n) AS node_count
}
CALL {
  MATCH (a:KnowledgeNode {group_id: $group_id})-[r]->(b:KnowledgeNode)
  WHERE r.group_id = $group_id OR b.group_id = $group_id
  RETURN collect(DISTINCT r) AS all_rels, count(DISTINCT r) AS rel_count
}
RETURN node_count, rel_count,
       [n IN all_nodes | {uid: n.uid, name: n.name, labels: n.labels}] AS node_samples,
       [r IN all_rels | {rel_type: coalesce(r.rel_type, type(r)), source: startNode(r).uid, target: endNode(r).uid}] AS rel_samples
"""

    _CLEAR_GROUP = """
MATCH (n:KnowledgeNode {group_id: $group_id})
DETACH DELETE n
RETURN count(*) AS deleted
"""

    def expand_neighbors(self, group_id: str, uid: str, depth: int = 1) -> dict[str, Any]:
        """查询指定节点的 N 跳邻居（双向）。"""
        safe_depth = max(1, min(depth, 5))
        query = self._NEIGHBORS_QUERY_TEMPLATE.format(depth=safe_depth)
        rows = self._run(query, {"group_id": group_id, "uid": uid})
        if not rows:
            return {"nodes": [], "relations": []}
        row = rows[0]
        nodes = [self._serialize_node(n) for n in (row.get("nodes") or [])]
        rels = []
        for r in (row.get("rels") or []):
            data = self._mapping(r)
            src_uid = ""
            tgt_uid = ""
            try:
                start = getattr(r, "start_node", lambda: None)()
                end = getattr(r, "end_node", lambda: None)()
                if start is not None:
                    src_uid = clean_text(start.get("uid", ""))
                if end is not None:
                    tgt_uid = clean_text(end.get("uid", ""))
            except Exception:
                pass
            rels.append({
                "id": clean_text(getattr(r, "element_id", "")),
                "source_uid": src_uid,
                "target_uid": tgt_uid,
                "rel_type": clean_text(data.get("rel_type")) or DEFAULT_REL_TYPE,
                "group_id": clean_text(data.get("group_id")),
                "description": clean_text(data.get("description")),
                "properties": {},
            })
        return {"nodes": [n for n in nodes if n], "relations": rels}

    def find_path(self, group_id: str, source_uid: str, target_uid: str) -> dict[str, Any]:
        """查找两节点间最短路径。"""
        rows = self._run(self._PATH_QUERY, {
            "group_id": group_id,
            "source_uid": source_uid,
            "target_uid": target_uid,
        })
        if not rows or not rows[0].get("nodes"):
            return {"nodes": [], "relations": [], "path_length": -1}
        row = rows[0]
        nodes = [self._serialize_node(n) for n in (row.get("nodes") or [])]
        rels = []
        raw_rels = row.get("rels") or []
        raw_nodes = row.get("nodes") or []
        for i, r in enumerate(raw_rels):
            data = self._mapping(r)
            src_uid = ""
            tgt_uid = ""
            if i < len(raw_nodes):
                src_uid = clean_text(self._mapping(raw_nodes[i]).get("uid"))
            if i + 1 < len(raw_nodes):
                tgt_uid = clean_text(self._mapping(raw_nodes[i + 1]).get("uid"))
            rels.append({
                "id": clean_text(getattr(r, "element_id", "")),
                "source_uid": src_uid,
                "target_uid": tgt_uid,
                "rel_type": clean_text(data.get("rel_type")) or DEFAULT_REL_TYPE,
                "group_id": clean_text(data.get("group_id")),
                "description": clean_text(data.get("description")),
                "properties": {},
            })
        return {
            "nodes": [n for n in nodes if n],
            "relations": rels,
            "path_length": int(row.get("path_length", -1) or -1),
        }

    def get_stats(self, group_id: str) -> dict[str, Any]:
        """获取图谱统计摘要。"""
        rows = self._run(self._STATS_QUERY, {"group_id": group_id})
        if not rows:
            return {"node_count": 0, "rel_count": 0, "node_types": {}, "rel_types": {}, "orphan_count": 0}
        row = rows[0]
        node_count = int(row.get("node_count", 0) or 0)
        rel_count = int(row.get("rel_count", 0) or 0)
        node_types: dict[str, int] = {}
        for n in (row.get("node_samples") or []):
            for label in (n.get("labels") or []):
                label = clean_text(label)
                if label:
                    node_types[label] = node_types.get(label, 0) + 1
        rel_types: dict[str, int] = {}
        connected_uids: set[str] = set()
        for r in (row.get("rel_samples") or []):
            rt = clean_text(r.get("rel_type"))
            if rt:
                rel_types[rt] = rel_types.get(rt, 0) + 1
            s = clean_text(r.get("source"))
            t = clean_text(r.get("target"))
            if s:
                connected_uids.add(s)
            if t:
                connected_uids.add(t)
        orphan_count = max(0, node_count - len(connected_uids))
        return {
            "node_count": node_count,
            "rel_count": rel_count,
            "node_types": node_types,
            "rel_types": rel_types,
            "orphan_count": orphan_count,
        }

    def clear_group(self, group_id: str) -> int:
        """删除指定 group_id 下的全部节点和关系。"""
        rows = self._run(self._CLEAR_GROUP, {"group_id": group_id}, write=True)
        return int((rows[0] if rows else {}).get("deleted", 0) or 0)

    def export_all(self, group_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """导出指定 group_id 下的全部节点和关系。"""
        nodes: dict[str, dict[str, Any]] = {}
        rels: list[dict[str, Any]] = []
        page_size = 500
        page = 1
        while True:
            data = self.query_graph(group_id, page, page_size)
            for n in data["nodes"]:
                nodes[n["uid"]] = n
            rels.extend(data["relations"])
            if not data["nodes_has_more"] and not data["relations_has_more"] and len(data["nodes"]) == 0 and len(data["relations"]) == 0:
                break
            page += 1
        return list(nodes.values()), rels

    def import_all(
        self, group_id: str, nodes: list[dict[str, Any]], relations: list[dict[str, Any]],
        settings: Mapping[str, Any],
    ) -> dict[str, Any]:
        """批量导入节点和关系到指定 group_id，返回统计信息。"""
        node_count = 0
        for n in nodes:
            n["group_id"] = group_id
            self.create_node(n)
            node_count += 1

        rel_count = 0
        skipped = 0
        known_ids = {clean_text(n.get("uid")) for n in nodes}
        for r in relations:
            r["group_id"] = group_id
            src = clean_text(r.get("source_uid"))
            tgt = clean_text(r.get("target_uid"))
            if src not in known_ids or tgt not in known_ids:
                skipped += 1
                continue
            self.create_relation(r)
            rel_count += 1

        return {"nodes_imported": node_count, "relations_imported": rel_count, "relations_skipped": skipped}

    def merge_import(
        self, group_id: str, nodes: list[dict[str, Any]], relations: list[dict[str, Any]],
        settings: Mapping[str, Any],
    ) -> dict[str, Any]:
        """按 uid MERGE 节点，按 src+tgt MERGE 关系，不删除已有数据。"""
        known_ids = self._collect_group_uids(group_id)
        node_count = 0
        for n in nodes:
            n["group_id"] = group_id
            self.create_node(n)
            uid = clean_text(n.get("uid"))
            if uid:
                known_ids.add(uid)
            node_count += 1

        rel_count = 0
        skipped = 0
        for r in relations:
            r["group_id"] = group_id
            src = clean_text(r.get("source_uid"))
            tgt = clean_text(r.get("target_uid"))
            if src not in known_ids or tgt not in known_ids:
                skipped += 1
                continue
            params = self._relation_create_params(r)
            self._run(self._MERGE_REL, params, write=True)
            rel_count += 1

        return {"nodes_imported": node_count, "relations_imported": rel_count, "relations_skipped": skipped}

    def _collect_group_uids(self, group_id: str) -> set[str]:
        """收集指定 group_id 下的全部节点 uid，用于关系导入前校验。"""
        uids: set[str] = set()
        page_size = 500
        page = 1
        while True:
            offset = (page - 1) * page_size
            rows = self._run(
                "MATCH (n:KnowledgeNode {group_id: $group_id}) RETURN n.uid AS uid SKIP $offset LIMIT $limit",
                {"group_id": group_id, "offset": offset, "limit": page_size},
            )
            for row in rows:
                uid = clean_text(row.get("uid"))
                if uid:
                    uids.add(uid)
            if len(rows) < page_size:
                break
            page += 1
        return uids

    def close(self) -> None:
        """关闭 Neo4j driver。"""
        if self._driver is not None:
            self._driver.close()
            self._driver = None

    def _serialize_node(self, value: Any, explicit_labels: Any = None) -> dict[str, Any] | None:
        if value is None:
            return None
        data = self._mapping(value)
        uid = clean_text(data.get("uid"))
        if not uid:
            return None
        labels = self._str_list(data.get("labels"))
        explicit = self._str_list(explicit_labels)
        if explicit:
            labels = explicit
        if not labels and hasattr(value, "labels"):
            try:
                labels = self._str_list(list(getattr(value, "labels")))
            except Exception:
                labels = []
        if labels:
            labels = [label for label in labels if label != "KnowledgeNode"]
        properties = extract_properties(data, NODE_RESERVED_PROP_KEYS)
        meta, properties = split_meta_from_props(properties)
        return {
            "uid": uid,
            "name": clean_text(data.get("name")) or uid,
            "group_id": clean_text(data.get("group_id")),
            "description": clean_text(data.get("description")),
            "labels": labels,
            "properties": properties,
            "meta": meta,
        }

    def _serialize_relation(
        self,
        rel_obj: Any,
        src_node: dict[str, Any] | None,
        tgt_node: dict[str, Any] | None,
        explicit_type: Any = None,
        explicit_id: Any = None,
    ) -> dict[str, Any] | None:
        if rel_obj is None or src_node is None or tgt_node is None:
            return None
        data = self._mapping(rel_obj)
        rel_type = clean_text(data.get("rel_type"))

        if not rel_type or rel_type == DEFAULT_REL_TYPE:
            neo_type = clean_text(explicit_type) if explicit_type else ""
            if not neo_type and hasattr(rel_obj, "type"):
                neo_type = clean_text(getattr(rel_obj, "type"))
            if neo_type and neo_type != DEFAULT_REL_TYPE:
                rel_type = neo_type

        rel_id = clean_text(explicit_id) if explicit_id else ""
        if not rel_id and hasattr(rel_obj, "element_id"):
            rel_id = clean_text(getattr(rel_obj, "element_id", ""))

        return {
            "id": rel_id,
            "source_uid": src_node.get("uid"),
            "target_uid": tgt_node.get("uid"),
            "rel_type": rel_type or DEFAULT_REL_TYPE,
            "group_id": clean_text(data.get("group_id")),
            "description": clean_text(data.get("description")),
            "properties": extract_properties(data, RELATION_RESERVED_PROP_KEYS),
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
            text = clean_text(item)
            if text:
                result.append(text)
        return result
