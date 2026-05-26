from __future__ import annotations

import logging
import time
import threading
from collections.abc import Mapping
from typing import Any, cast

from neo4j import Driver, GraphDatabase

from core.constants import DEFAULT_REL_TYPE, NODE_RESERVED_PROP_KEYS, RELATION_RESERVED_PROP_KEYS
from core.graph_query_common import _ensure_group_id_index, _ensure_group_id_prop
from core.graph_write_common import node_payload_to_cypher_row, relation_payload_to_cypher_row
from core.types import NodePayload, RelationPayload, clean_text, extract_properties, split_meta_from_props


class GroupGraphStore:
    """封装 group 图谱的 Neo4j 读写逻辑。"""

    _log = logging.getLogger("GroupGraphStore")

    # ── 读查询：不含 OR，不限关系类型 ──────────────────────────────────────

    _COUNT_QUERY = """
CALL {
  MATCH (n:KnowledgeNode {group_id: $group_id})
  RETURN count(DISTINCT n) AS node_count
}
CALL {
  MATCH (src:KnowledgeNode {group_id: $group_id})-[r]->(tgt:KnowledgeNode)
  WHERE r.group_id = $group_id
  RETURN count(DISTINCT r) AS rel_count
}
RETURN node_count, rel_count
"""

    _NODES_QUERY = """
MATCH (n:KnowledgeNode {group_id: $group_id})
WITH DISTINCT n
WHERE $after_id = '' OR elementId(n) > $after_id
RETURN n, labels(n) AS neo_labels, elementId(n) AS nid
ORDER BY elementId(n)
LIMIT $limit
"""

    _RELS_QUERY = """
MATCH (src:KnowledgeNode {group_id: $group_id})-[r]->(tgt:KnowledgeNode)
WHERE r.group_id = $group_id
WITH DISTINCT r, src, tgt
WHERE $after_id = '' OR elementId(r) > $after_id
RETURN src.nid AS src_nid, tgt.nid AS tgt_nid,
       properties(r) AS rel_props, type(r) AS rel_type, elementId(r) AS rel_id
ORDER BY elementId(r)
LIMIT $limit
"""

    _BATCH_NODES_QUERY = """
UNWIND $nids AS nid
MATCH (n:KnowledgeNode {nid: nid, group_id: $group_id})
RETURN n, labels(n) AS neo_labels
"""

    # ── 写查询：不限关系类型，用属性匹配 ──────────────────────────────────

    _UPSERT_NODE = """
MERGE (n:KnowledgeNode {nid: $nid, group_id: $group_id})
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
MATCH (n:KnowledgeNode {nid: $nid, group_id: $group_id})
DETACH DELETE n
RETURN count(*) AS deleted
"""

    _CREATE_REL = """
MATCH (src:KnowledgeNode {nid: $source_nid, group_id: $group_id})
MATCH (tgt:KnowledgeNode {nid: $target_nid, group_id: $group_id})
CREATE (src)-[r:RELATED {
  rel_type: $rel_type,
  group_id: $group_id
}]->(tgt)
SET r += $props
RETURN elementId(r) AS relation_id
"""

    _MERGE_REL = """
MATCH (src:KnowledgeNode {nid: $source_nid, group_id: $group_id})
MATCH (tgt:KnowledgeNode {nid: $target_nid, group_id: $group_id})
MERGE (src)-[r:RELATED]->(tgt)
SET r.rel_type = $rel_type,
    r.group_id = $group_id
SET r += $props
RETURN elementId(r) AS relation_id
"""

    _UPDATE_REL_BY_ENDPOINTS = """
MATCH (src:KnowledgeNode {nid: $source_nid, group_id: $group_id})-[r]->(tgt:KnowledgeNode {nid: $target_nid, group_id: $group_id})
WHERE r.group_id = $group_id
SET r.rel_type = $rel_type,
    r.group_id = $group_id,
    r.description = $description
REMOVE r.properties, r.meta
SET r += $props
RETURN elementId(r) AS relation_id
"""

    _DELETE_REL_BY_ENDPOINTS = """
MATCH (src:KnowledgeNode {nid: $source_nid, group_id: $group_id})-[r]->(tgt:KnowledgeNode {nid: $target_nid, group_id: $group_id})
WHERE r.group_id = $group_id
DELETE r
RETURN count(*) AS deleted
"""

    _REDIRECT_OUTGOING_RELS = """
MATCH (old:KnowledgeNode {nid: $old_nid, group_id: $group_id})-[r]->(tgt:KnowledgeNode)
WHERE r.group_id = $group_id
  AND (tgt.nid <> $new_nid OR tgt.group_id <> $group_id)
WITH old, tgt, r, type(r) AS rType, properties(r) AS rProps
DELETE r
WITH old, tgt, rType, rProps
MERGE (new:KnowledgeNode {nid: $new_nid, group_id: $group_id})
CALL apoc.create.relationship(new, rType, rProps, tgt) YIELD rel
SET rel.group_id = $group_id
RETURN count(*) AS redirected
"""

    _REDIRECT_OUTGOING_RELS_FALLBACK = """
MATCH (old:KnowledgeNode {nid: $old_nid, group_id: $group_id})-[r]->(tgt:KnowledgeNode)
WHERE r.group_id = $group_id
  AND (tgt.nid <> $new_nid OR tgt.group_id <> $group_id)
WITH old, tgt, r, properties(r) AS rProps
DELETE r
WITH old, tgt, rProps
MERGE (new:KnowledgeNode {nid: $new_nid, group_id: $group_id})
CREATE (new)-[nr:RELATED]->(tgt)
SET nr = rProps
SET nr.group_id = $group_id
RETURN count(*) AS redirected
"""

    _REDIRECT_INCOMING_RELS = """
MATCH (src:KnowledgeNode)-[r]->(old:KnowledgeNode {nid: $old_nid, group_id: $group_id})
WHERE r.group_id = $group_id
  AND (src.nid <> $new_nid OR src.group_id <> $group_id)
WITH src, old, r, type(r) AS rType, properties(r) AS rProps
DELETE r
WITH src, old, rType, rProps
MERGE (new:KnowledgeNode {nid: $new_nid, group_id: $group_id})
CALL apoc.create.relationship(src, rType, rProps, new) YIELD rel
SET rel.group_id = $group_id
RETURN count(*) AS redirected
"""

    _REDIRECT_INCOMING_RELS_FALLBACK = """
MATCH (src:KnowledgeNode)-[r]->(old:KnowledgeNode {nid: $old_nid, group_id: $group_id})
WHERE r.group_id = $group_id
  AND (src.nid <> $new_nid OR src.group_id <> $group_id)
WITH src, old, r, properties(r) AS rProps
DELETE r
WITH src, old, rProps
MERGE (new:KnowledgeNode {nid: $new_nid, group_id: $group_id})
CREATE (src)-[nr:RELATED]->(new)
SET nr = rProps
SET nr.group_id = $group_id
RETURN count(*) AS redirected
"""

    # ── 统计查询：不含 OR ────────────────────────────────────────────────

    _STATS_QUERY = """
CALL {
  MATCH (n:KnowledgeNode {group_id: $group_id})
  RETURN collect(DISTINCT n) AS all_nodes, count(DISTINCT n) AS node_count
}
CALL {
  MATCH (a:KnowledgeNode {group_id: $group_id})-[r]->(b:KnowledgeNode)
  WHERE r.group_id = $group_id
  RETURN collect(DISTINCT r) AS all_rels, count(DISTINCT r) AS rel_count
}
RETURN node_count, rel_count,
       [n IN all_nodes | {nid: n.nid, name: n.name, labels: n.labels}] AS node_samples,
       [r IN all_rels | {rel_type: coalesce(r.rel_type, type(r)), source: startNode(r).nid, target: endNode(r).nid}] AS rel_samples
"""

    _CLEAR_GROUP = """
MATCH (n:KnowledgeNode {group_id: $group_id})
DETACH DELETE n
RETURN count(*) AS deleted
"""

    # ── 初始化 ──────────────────────────────────────────────────────────

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

    def _session_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {}
        if self.database:
            kwargs["database"] = self.database
        return kwargs

    # ── 查询图谱（游标 + 分页兼容 + 三路并行）──────────────────────────

    def query_graph(
        self,
        group_id: str,
        page: int = 1,
        page_size: int = 300,
        node_cursor: str = "",
        rel_cursor: str = "",
    ) -> dict[str, Any]:
        """分页查询 group_id 下的图谱节点与关系。"""
        self._ensure_driver()
        assert self._driver is not None
        limit = page_size + 1
        use_cursor = bool(node_cursor or rel_cursor)
        kwargs = self._session_kwargs()

        if not use_cursor and page > 1:
            self._log.warning("使用 SKIP 分页 (page=%d)，建议改用游标参数 node_cursor/rel_cursor", page)

        is_first = use_cursor or page == 1

        nodes_total = -1
        relations_total = -1
        db_timings: dict[str, Any] = {}

        count_rows: list[dict[str, Any]] = []
        node_rows: list[dict[str, Any]] = []
        rel_rows: list[dict[str, Any]] = []
        err_box: list[Exception] = []

        def _fetch_count() -> None:
            try:
                with self._driver.session(**kwargs) as s:  # type: ignore[union-attr]
                    count_rows.extend(
                        record.data() for record in s.run(self._COUNT_QUERY, {"group_id": group_id})
                    )
            except Exception as e:
                err_box.append(e)

        def _fetch_nodes() -> None:
            try:
                with self._driver.session(**kwargs) as s:  # type: ignore[union-attr]
                    if use_cursor and not node_cursor:
                        return  # 节点已取完，跳过
                    after = node_cursor if use_cursor else ""
                    params = {"group_id": group_id, "after_id": after, "limit": limit}
                    node_rows.extend(record.data() for record in s.run(self._NODES_QUERY, params))
            except Exception as e:
                err_box.append(e)

        def _fetch_rels() -> None:
            try:
                with self._driver.session(**kwargs) as s:  # type: ignore[union-attr]
                    if use_cursor and not rel_cursor:
                        return  # 关系已取完，跳过
                    after = rel_cursor if use_cursor else ""
                    params = {"group_id": group_id, "after_id": after, "limit": limit}
                    rel_rows.extend(record.data() for record in s.run(self._RELS_QUERY, params))
            except Exception as e:
                err_box.append(e)

        threads: list[threading.Thread] = []
        if is_first:
            threads.append(threading.Thread(target=_fetch_count))
        threads.append(threading.Thread(target=_fetch_nodes))
        threads.append(threading.Thread(target=_fetch_rels))

        t0 = time.perf_counter()
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        if err_box:
            raise err_box[0]
        db_timings["data"] = round((time.perf_counter() - t0) * 1000, 1)

        if is_first and count_rows:
            cr = count_rows[0]
            nodes_total = int(cr.get("node_count", 0) or 0)
            relations_total = int(cr.get("rel_count", 0) or 0)
            db_timings["count_raw"] = cr

        # ── 分页 ──

        nodes_has_more = len(node_rows) > page_size
        relations_has_more = len(rel_rows) > page_size
        if nodes_has_more:
            node_rows = node_rows[:page_size]
        if relations_has_more:
            rel_rows = rel_rows[:page_size]

        # ── 序列化节点 ──

        nodes: dict[str, dict[str, Any]] = {}
        for row in node_rows:
            node = self._serialize_node(row.get("n"), explicit_labels=row.get("neo_labels"))
            if node:
                nodes[node["nid"]] = node

        # ── 序列化关系（仅 nid + properties，不含完整节点）──

        rels: list[dict[str, Any]] = []
        missing_nids: set[str] = set()
        rel_raw: list[tuple[str, str, dict[str, Any], str, str]] = []

        for row in rel_rows:
            src_nid = clean_text(row.get("src_nid"))
            tgt_nid = clean_text(row.get("tgt_nid"))
            rel_props = row.get("rel_props") or {}
            rel_type = clean_text(row.get("rel_type"))
            rel_id = clean_text(row.get("rel_id"))
            if src_nid:
                rel_raw.append((src_nid, tgt_nid, rel_props, rel_type, rel_id))
                if src_nid not in nodes:
                    missing_nids.add(src_nid)
                if tgt_nid and tgt_nid not in nodes:
                    missing_nids.add(tgt_nid)

        if missing_nids:
            for node in self._batch_load_nodes(group_id, missing_nids):
                nodes[node["nid"]] = node

        for src_nid, tgt_nid, rel_props, rel_type, rel_id in rel_raw:
            src_node = nodes.get(src_nid)
            tgt_node = nodes.get(tgt_nid)
            if not src_node or not tgt_node:
                continue
            rels.append({
                "id": rel_id,
                "source_nid": src_nid,
                "target_nid": tgt_nid,
                "rel_type": rel_type or DEFAULT_REL_TYPE,
                "group_id": clean_text(rel_props.get("group_id")),
                "description": clean_text(rel_props.get("description")),
                "properties": {k: v for k, v in rel_props.items()
                               if k not in RELATION_RESERVED_PROP_KEYS},
            })

        # ── 游标 ──

        next_node_cursor = ""
        next_rel_cursor = ""
        if node_rows:
            next_node_cursor = clean_text(node_rows[-1].get("nid", ""))
        if rel_rows:
            next_rel_cursor = clean_text(rel_rows[-1].get("rel_id", ""))

        result: dict[str, Any] = {
            "group_id": group_id,
            "page": page,
            "page_size": page_size,
            "nodes": list(nodes.values()),
            "relations": rels,
            "nodes_total": nodes_total,
            "relations_total": relations_total,
            "nodes_count": len(nodes),
            "relations_count": len(rels),
            "nodes_has_more": nodes_has_more,
            "relations_has_more": relations_has_more,
            "db_timings": db_timings,
            "next_node_cursor": next_node_cursor,
            "next_rel_cursor": next_rel_cursor,
        }
        return result

    def _batch_load_nodes(self, group_id: str, nids: set[str]) -> list[dict[str, Any]]:
        """批量补查缺失节点。"""
        if not nids:
            return []
        kwargs = self._session_kwargs()
        assert self._driver is not None
        nodes: list[dict[str, Any]] = []
        with self._driver.session(**kwargs) as session:
            for row in session.run(self._BATCH_NODES_QUERY, {"nids": list(nids), "group_id": group_id}):
                node = self._serialize_node(row.get("n"), explicit_labels=row.get("neo_labels"))
                if node:
                    nodes.append(node)
        return nodes

    # ── CRUD ────────────────────────────────────────────────────────────

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
        """按 nid + group_id 删除节点。"""
        params = {
            "group_id": clean_text(payload.get("group_id")),
            "nid": clean_text(payload.get("nid")),
        }
        rows = self._run(self._DELETE_NODE, params, write=True)
        return int((rows[0] if rows else {}).get("deleted", 0) or 0)

    def create_relation(self, payload: Mapping[str, Any]) -> str:
        """新增关系并返回 element id。"""
        params = self._relation_create_params(payload)
        rows = self._run(self._CREATE_REL, params, write=True)
        if rows:
            return f"{params['source_nid']}->{params['target_nid']}"
        return ""

    def update_relation(self, payload: Mapping[str, Any]) -> str:
        """通过 group_id + source_nid + target_nid 更新关系并返回引用键。"""
        params = self._relation_update_params(payload)
        rows = self._run(self._UPDATE_REL_BY_ENDPOINTS, params, write=True)
        if rows:
            return f"{params['source_nid']}->{params['target_nid']}"
        return ""

    def delete_relation(self, payload: Mapping[str, Any]) -> int:
        """通过 group_id + source_nid + target_nid 删除关系。"""
        group_id = clean_text(payload.get("group_id"))
        source_nid = clean_text(payload.get("source_nid"))
        target_nid = clean_text(payload.get("target_nid"))
        rows = self._run(
            self._DELETE_REL_BY_ENDPOINTS,
            {"group_id": group_id, "source_nid": source_nid, "target_nid": target_nid},
            write=True,
        )
        return int((rows[0] if rows else {}).get("deleted", 0) or 0)

    def replace_node_relations(self, group_id: str, old_nid: str, new_nid: str) -> int:
        """将 old_nid 节点的全部关系转移至 new_nid 节点，返回转移的关系数。"""
        if old_nid == new_nid:
            return 0
        params = {"group_id": group_id, "old_nid": old_nid, "new_nid": new_nid}
        out_count = self._try_redirect(self._REDIRECT_OUTGOING_RELS, self._REDIRECT_OUTGOING_RELS_FALLBACK, params)
        in_count = self._try_redirect(self._REDIRECT_INCOMING_RELS, self._REDIRECT_INCOMING_RELS_FALLBACK, params)
        return out_count + in_count

    def _try_redirect(self, primary: str, fallback: str, params: dict[str, Any]) -> int:
        """优先使用 APOC，失败则降级为固定类型。"""
        try:
            rows = self._run(primary, params, write=True)
            return int((rows[0] if rows else {}).get("redirected", 0) or 0)
        except Exception:
            self._log.warning("APOC 不可用，降级为 RELATED 固定类型")
            rows = self._run(fallback, params, write=True)
            return int((rows[0] if rows else {}).get("redirected", 0) or 0)

    # ── 参数构建 ────────────────────────────────────────────────────────

    def _node_params(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        row = node_payload_to_cypher_row(
            cast(NodePayload, {
                "nid": clean_text(payload.get("nid")),
                "name": clean_text(payload.get("name")) or clean_text(payload.get("nid")),
                "labels": self._str_list(payload.get("labels")) or ["Node"],
                "description": clean_text(payload.get("description")),
                "group_id": clean_text(payload.get("group_id")),
                "properties": payload.get("properties") if isinstance(payload.get("properties"), Mapping) else {},
                "meta": payload.get("meta") if isinstance(payload.get("meta"), Mapping) else {},
            })
        )
        return {
            "group_id": row["group_id"],
            "nid": row["nid"],
            "name": row["name"],
            "description": row["description"],
            "labels": row["labels"],
            "props": row["props"],
            "embedding": row["embedding"],
        }

    def _relation_create_params(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        row = relation_payload_to_cypher_row(
            cast(RelationPayload, {
                "source_nid": clean_text(payload.get("source_nid")),
                "target_nid": clean_text(payload.get("target_nid")),
                "rel_type": clean_text(payload.get("rel_type")) or "RELATED",
                "direction": "forward",
                "group_id": clean_text(payload.get("group_id")),
                "description": clean_text(payload.get("description")),
                "properties": payload.get("properties") if isinstance(payload.get("properties"), Mapping) else {},
                "meta": payload.get("meta") if isinstance(payload.get("meta"), Mapping) else {},
            })
        )
        return {
            "source_nid": row["source_nid"],
            "target_nid": row["target_nid"],
            "rel_type": row["rel_type"],
            "group_id": row["group_id"],
            "props": row["props"],
        }

    def _relation_update_params(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        group_id = clean_text(payload.get("group_id"))
        source_nid = clean_text(payload.get("source_nid"))
        target_nid = clean_text(payload.get("target_nid"))
        rel_type = clean_text(payload.get("rel_type")) or "RELATED"
        if not group_id:
            raise ValueError("group_id 不能为空")
        if not source_nid:
            raise ValueError("source_nid 不能为空")
        if not target_nid:
            raise ValueError("target_nid 不能为空")

        input_props = payload.get("properties")
        input_meta = payload.get("meta")
        description = clean_text(payload.get("description"))
        direction = clean_text(payload.get("direction")) or "forward"
        weight = payload.get("weight")
        if not isinstance(weight, (int, float)):
            weight = None

        row = relation_payload_to_cypher_row(
            cast(RelationPayload, {
                "source_nid": source_nid,
                "target_nid": target_nid,
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
            "source_nid": source_nid,
            "target_nid": target_nid,
            "rel_type": row["rel_type"],
            "group_id": row["group_id"],
            "description": row["props"].get("description", ""),
            "props": row["props"],
        }

    # ── 通用查询执行 ────────────────────────────────────────────────────

    def _run(self, query: str, parameters: dict[str, Any], write: bool = False) -> list[dict[str, Any]]:
        self._ensure_driver()
        kwargs = self._session_kwargs()
        assert self._driver is not None
        with self._driver.session(**kwargs) as session:
            result = session.run(query, parameters)  # type: ignore[arg-type]
            rows = [record.data() for record in result]
            if write:
                result.consume()
            return rows

    # ── 邻居/路径/统计 ─────────────────────────────────────────────────

    _NEIGHBORS_QUERY_TEMPLATE = """
MATCH (start:KnowledgeNode {{nid: $nid, group_id: $group_id}})
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
MATCH (src:KnowledgeNode {nid: $source_nid, group_id: $group_id}),
      (tgt:KnowledgeNode {nid: $target_nid, group_id: $group_id})
MATCH path = shortestPath((src)-[*..15]-(tgt))
RETURN [n IN nodes(path) | n] AS nodes,
       [r IN relationships(path) | r] AS rels,
       length(path) AS path_length
"""

    def expand_neighbors(self, group_id: str, nid: str, depth: int = 1) -> dict[str, Any]:
        """查询指定节点的 N 跳邻居（双向）。"""
        safe_depth = max(1, min(depth, 5))
        query = self._NEIGHBORS_QUERY_TEMPLATE.format(depth=safe_depth)
        rows = self._run(query, {"group_id": group_id, "nid": nid})
        if not rows:
            return {"nodes": [], "relations": []}
        row = rows[0]
        nodes = [self._serialize_node(n) for n in (row.get("nodes") or [])]
        rels = []
        for r in (row.get("rels") or []):
            data = self._mapping(r)
            src_nid = ""
            tgt_nid = ""
            try:
                start = getattr(r, "start_node", lambda: None)()
                end = getattr(r, "end_node", lambda: None)()
                if start is not None:
                    src_nid = clean_text(start.get("nid", ""))
                if end is not None:
                    tgt_nid = clean_text(end.get("nid", ""))
            except Exception:
                pass
            rels.append({
                "id": clean_text(getattr(r, "element_id", "")),
                "source_nid": src_nid,
                "target_nid": tgt_nid,
                "rel_type": clean_text(data.get("rel_type")) or DEFAULT_REL_TYPE,
                "group_id": clean_text(data.get("group_id")),
                "description": clean_text(data.get("description")),
                "properties": {},
            })
        return {"nodes": [n for n in nodes if n], "relations": rels}

    def find_path(self, group_id: str, source_nid: str, target_nid: str) -> dict[str, Any]:
        """查找两节点间最短路径。"""
        rows = self._run(self._PATH_QUERY, {
            "group_id": group_id,
            "source_nid": source_nid,
            "target_nid": target_nid,
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
            src_nid = ""
            tgt_nid = ""
            if i < len(raw_nodes):
                src_nid = clean_text(self._mapping(raw_nodes[i]).get("nid"))
            if i + 1 < len(raw_nodes):
                tgt_nid = clean_text(self._mapping(raw_nodes[i + 1]).get("nid"))
            rels.append({
                "id": clean_text(getattr(r, "element_id", "")),
                "source_nid": src_nid,
                "target_nid": tgt_nid,
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
        connected_nids: set[str] = set()
        for r in (row.get("rel_samples") or []):
            rt = clean_text(r.get("rel_type"))
            if rt:
                rel_types[rt] = rel_types.get(rt, 0) + 1
            s = clean_text(r.get("source"))
            t = clean_text(r.get("target"))
            if s:
                connected_nids.add(s)
            if t:
                connected_nids.add(t)
        orphan_count = max(0, node_count - len(connected_nids))
        return {
            "node_count": node_count,
            "rel_count": rel_count,
            "node_types": node_types,
            "rel_types": rel_types,
            "orphan_count": orphan_count,
        }

    # ── 清空 / 导出 / 导入 ─────────────────────────────────────────────

    def clear_group(self, group_id: str) -> int:
        """删除指定 group_id 下的全部节点和关系。"""
        rows = self._run(self._CLEAR_GROUP, {"group_id": group_id}, write=True)
        return int((rows[0] if rows else {}).get("deleted", 0) or 0)

    def export_all(self, group_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """导出指定 group_id 下的全部节点和关系。"""
        nodes: dict[str, dict[str, Any]] = {}
        rels: list[dict[str, Any]] = []
        page_size = 500
        nc, rc = "", ""
        while True:
            data = self.query_graph(group_id, page_size=page_size, node_cursor=nc, rel_cursor=rc)
            for n in data["nodes"]:
                nodes[n["nid"]] = n
            rels.extend(data["relations"])
            if not data["nodes_has_more"] and not data["relations_has_more"]:
                break
            nc = data.get("next_node_cursor", "")
            rc = data.get("next_rel_cursor", "")
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
        known_ids = {clean_text(n.get("nid")) for n in nodes}
        for r in relations:
            r["group_id"] = group_id
            src = clean_text(r.get("source_nid"))
            tgt = clean_text(r.get("target_nid"))
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
        """按 nid MERGE 节点，按 src+tgt MERGE 关系，不删除已有数据。"""
        known_ids = self._collect_group_nids(group_id)
        node_count = 0
        for n in nodes:
            n["group_id"] = group_id
            self.create_node(n)
            nid = clean_text(n.get("nid"))
            if nid:
                known_ids.add(nid)
            node_count += 1

        rel_count = 0
        skipped = 0
        for r in relations:
            r["group_id"] = group_id
            src = clean_text(r.get("source_nid"))
            tgt = clean_text(r.get("target_nid"))
            if src not in known_ids or tgt not in known_ids:
                skipped += 1
                continue
            params = self._relation_create_params(r)
            self._run(self._MERGE_REL, params, write=True)
            rel_count += 1

        return {"nodes_imported": node_count, "relations_imported": rel_count, "relations_skipped": skipped}

    def _collect_group_nids(self, group_id: str) -> set[str]:
        """收集指定 group_id 下的全部节点 nid，用于关系导入前校验。"""
        nids: set[str] = set()
        page_size = 500
        page = 1
        while True:
            offset = (page - 1) * page_size
            rows = self._run(
                "MATCH (n:KnowledgeNode {group_id: $group_id}) RETURN n.nid AS nid SKIP $offset LIMIT $limit",
                {"group_id": group_id, "offset": offset, "limit": page_size},
            )
            for row in rows:
                nid = clean_text(row.get("nid"))
                if nid:
                    nids.add(nid)
            if len(rows) < page_size:
                break
            page += 1
        return nids

    # ── 关闭 ───────────────────────────────────────────────────────────

    def close(self) -> None:
        """关闭 Neo4j driver。"""
        if self._driver is not None:
            self._driver.close()
            self._driver = None

    # ── 序列化 ─────────────────────────────────────────────────────────

    def _serialize_node(self, value: Any, explicit_labels: Any = None) -> dict[str, Any] | None:
        if value is None:
            return None
        data = self._mapping(value)
        nid = clean_text(data.get("nid"))
        if not nid:
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
            "nid": nid,
            "name": clean_text(data.get("name")) or nid,
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
            "source_nid": src_node.get("nid"),
            "target_nid": tgt_node.get("nid"),
            "rel_type": rel_type or DEFAULT_REL_TYPE,
            "group_id": clean_text(data.get("group_id")),
            "description": clean_text(data.get("description")),
            "properties": extract_properties(data, RELATION_RESERVED_PROP_KEYS),
        }

    # ── 工具方法 ───────────────────────────────────────────────────────

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
