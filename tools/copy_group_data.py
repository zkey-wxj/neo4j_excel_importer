from __future__ import annotations

from collections.abc import Generator, Mapping
from typing import Any, cast

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from core.constants import (
    DEFAULT_DIRECTION,
    DEFAULT_NODE_LABEL,
    DEFAULT_REL_TYPE,
    NODE_RESERVED_PROP_KEYS,
    RELATION_RESERVED_PROP_KEYS,
)
from core.graph_query_common import as_mapping, run_read_queries
from core.graph_write_common import write_nodes, write_relations
from core.types import (
    GraphMeta,
    NodePayload,
    RelationPayload,
    clean_text,
    extract_properties,
    get_credentials,
    normalize_labels,
    split_meta_from_props,
)


class CopyGroupDataTool(Tool):
    """将 source group 数据复制到 target group，并处理 nid 冲突。"""

    _NODE_QUERY = """
MATCH (n:KnowledgeNode)
WHERE n.group_id = $group_id
RETURN n
ORDER BY n.name ASC, n.nid ASC
"""

    _REL_QUERY = """
MATCH (src:KnowledgeNode)-[r]->(tgt:KnowledgeNode)
WHERE (
  r.group_id = $group_id
  OR (src.group_id = $group_id AND tgt.group_id = $group_id)
)
RETURN src, r, tgt
ORDER BY src.nid ASC, tgt.nid ASC, type(r)
"""

    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        """读取 source_group_id 的图数据并复制到 target_group_id。"""
        source_group_id = clean_text(tool_parameters.get("source_group_id"))
        target_group_id = clean_text(tool_parameters.get("target_group_id"))
        batch_size = int(tool_parameters.get("batch_size") or 500)
        database = clean_text(tool_parameters.get("database"))

        if not source_group_id:
            yield self.create_text_message("❌ source_group_id 不能为空。")
            return
        if not target_group_id:
            yield self.create_text_message("❌ target_group_id 不能为空。")
            return
        if source_group_id == target_group_id:
            yield self.create_text_message("❌ source_group_id 与 target_group_id 不能相同。")
            return

        try:
            results = run_read_queries(
                self.runtime,
                [
                    {"query": self._NODE_QUERY, "parameters": {"group_id": source_group_id}, "limit": 100000},
                    {"query": self._REL_QUERY, "parameters": {"group_id": source_group_id}, "limit": 200000},
                    {"query": self._NODE_QUERY, "parameters": {"group_id": target_group_id}, "limit": 100000},
                ],
                database=database,
            )
            source_node_rows, source_rel_rows, target_node_rows = results
        except Exception as exc:
            yield self.create_text_message(f"❌ 读取分组数据失败：{exc}")
            return

        target_node_index = self._build_target_node_index(target_node_rows)
        nid_usage = self._build_target_nid_usage(target_node_rows)
        source_nid_to_target_nid: dict[str, str] = {}
        node_payloads: list[NodePayload] = []
        merged_nodes_count = 0
        renamed_nodes_count = 0
        direct_insert_nodes_count = 0

        # 先处理节点冲突策略：nid 重复时，判断 name；同名则合并，否则重命名。
        for row in source_node_rows:
            source_node_map = as_mapping(row.get("n"))
            source_nid = clean_text(source_node_map.get("nid"))
            source_name = clean_text(source_node_map.get("name")) or source_nid
            if not source_nid:
                continue

            target_nid = source_nid
            target_node_map = target_node_index.get(target_nid)
            if target_node_map:
                target_name = clean_text(target_node_map.get("name")) or target_nid
                if target_name == source_name:
                    merged_nodes_count += 1
                    node_payload = self._build_merged_node_payload(
                        target_node_map=target_node_map,
                        source_node_map=source_node_map,
                        target_group_id=target_group_id,
                    )
                else:
                    renamed_nodes_count += 1
                    target_nid = self._allocate_renamed_nid(source_nid, nid_usage)
                    node_payload = self._build_node_payload(
                        source_node_map,
                        final_nid=target_nid,
                        target_group_id=target_group_id,
                    )
            else:
                direct_insert_nodes_count += 1
                nid_usage.add(target_nid)
                node_payload = self._build_node_payload(
                    source_node_map,
                    final_nid=target_nid,
                    target_group_id=target_group_id,
                )

            source_nid_to_target_nid[source_nid] = target_nid
            if node_payload:
                node_payloads.append(node_payload)

        relation_payloads: list[RelationPayload] = []
        for row in source_rel_rows:
            relation_payload = self._build_relation_payload(
                row,
                target_group_id=target_group_id,
                nid_map=source_nid_to_target_nid,
            )
            if relation_payload:
                relation_payloads.append(relation_payload)

        if not node_payloads:
            yield self.create_text_message(f"❌ 未在 source_group_id={source_group_id} 下找到可复制节点。")
            return

        try:
            uri, user, pwd = get_credentials(self.runtime)
            nodes_count = write_nodes(uri, user, pwd, node_payloads, batch_size=batch_size)
            rels_count = write_relations(uri, user, pwd, relation_payloads, batch_size=batch_size)
        except Exception as exc:
            yield self.create_text_message(f"❌ 复制写入失败：{exc}")
            return

        summary = (
            f"已将 group `{source_group_id}` 复制到 `{target_group_id}`，"
            f"节点写入 {nodes_count} 条（同 nid 同名合并 {merged_nodes_count} 条，"
            f"冲突重命名 {renamed_nodes_count} 条，直接新增 {direct_insert_nodes_count} 条），"
            f"关系写入 {rels_count} 条。"
        )
        yield self.create_variable_message("source_group_id", source_group_id)
        yield self.create_variable_message("target_group_id", target_group_id)
        yield self.create_variable_message("nodes_count", nodes_count)
        yield self.create_variable_message("rels_count", rels_count)
        yield self.create_variable_message("merged_nodes_count", merged_nodes_count)
        yield self.create_variable_message("renamed_nodes_count", renamed_nodes_count)
        yield self.create_variable_message("direct_insert_nodes_count", direct_insert_nodes_count)
        yield self.create_variable_message("summary", summary)
        yield self.create_text_message(f"✅ {summary}")

    def _build_target_node_index(self, target_node_rows: list[dict[str, Any]]) -> dict[str, Mapping[str, Any]]:
        """按 nid 建立目标组节点索引。"""
        index: dict[str, Mapping[str, Any]] = {}
        for row in target_node_rows:
            node_map = as_mapping(row.get("n"))
            nid = clean_text(node_map.get("nid"))
            if nid and nid not in index:
                index[nid] = node_map
        return index

    def _build_target_nid_usage(self, target_node_rows: list[dict[str, Any]]) -> set[str]:
        """提取目标组已占用 nid 集合，用于冲突重命名。"""
        used: set[str] = set()
        for row in target_node_rows:
            node_map = as_mapping(row.get("n"))
            nid = clean_text(node_map.get("nid"))
            if nid:
                used.add(nid)
        return used

    def _allocate_renamed_nid(self, source_nid: str, used_nids: set[str]) -> str:
        """为冲突节点生成新 nid，格式 nid__copy_N。"""
        suffix = 1
        while True:
            candidate = f"{source_nid}__copy_{suffix}"
            if candidate not in used_nids:
                used_nids.add(candidate)
                return candidate
            suffix += 1

    def _build_node_payload(
        self,
        node_map: Mapping[str, Any],
        *,
        final_nid: str,
        target_group_id: str,
    ) -> NodePayload | None:
        """将源节点映射为目标节点。"""
        source_name = clean_text(node_map.get("name")) or final_nid
        labels = normalize_labels(node_map.get("labels"))
        properties = extract_properties(node_map, NODE_RESERVED_PROP_KEYS)
        meta, clean_props = split_meta_from_props(properties)

        payload: NodePayload = {
            "nid": final_nid,
            "name": source_name,
            "labels": labels or [DEFAULT_NODE_LABEL],
            "description": clean_text(node_map.get("description")),
            "group_id": target_group_id,
            "properties": clean_props,
            "meta": cast(GraphMeta, meta),
        }
        embedding = node_map.get("embedding")
        if isinstance(embedding, list) and embedding:
            vector = [float(item) for item in embedding if isinstance(item, (int, float))]
            if vector:
                payload["embedding"] = vector
        return payload

    def _build_merged_node_payload(
        self,
        *,
        target_node_map: Mapping[str, Any],
        source_node_map: Mapping[str, Any],
        target_group_id: str,
    ) -> NodePayload:
        """同 nid 同 name 时合并属性：labels 取并集，properties/source 覆盖缺失字段。"""
        nid = clean_text(target_node_map.get("nid"))
        name = clean_text(target_node_map.get("name")) or nid
        target_labels = normalize_labels(target_node_map.get("labels"))
        source_labels = normalize_labels(source_node_map.get("labels"))
        merged_labels = list(target_labels)
        for label in source_labels:
            if label not in merged_labels:
                merged_labels.append(label)

        target_props = extract_properties(target_node_map, NODE_RESERVED_PROP_KEYS)
        target_meta, target_clean_props = split_meta_from_props(target_props)
        source_props = extract_properties(source_node_map, NODE_RESERVED_PROP_KEYS)
        source_meta, source_clean_props = split_meta_from_props(source_props)

        merged_props = dict(target_clean_props)
        for key, value in source_clean_props.items():
            merged_props[key] = self._accumulate_value(merged_props.get(key), value)
            if merged_props[key] in (None, ""):
                merged_props.pop(key, None)

        merged_meta = dict(target_meta)
        for key, value in source_meta.items():
            merged_meta[key] = self._accumulate_value(merged_meta.get(key), value)
            if merged_meta[key] in (None, ""):
                merged_meta.pop(key, None)

        description = clean_text(target_node_map.get("description")) or clean_text(source_node_map.get("description"))
        payload: NodePayload = {
            "nid": nid,
            "name": name,
            "labels": merged_labels or [DEFAULT_NODE_LABEL],
            "description": description,
            "group_id": target_group_id,
            "properties": merged_props,
            "meta": cast(GraphMeta, merged_meta),
        }

        target_embedding = target_node_map.get("embedding")
        source_embedding = source_node_map.get("embedding")
        chosen_embedding = target_embedding if isinstance(target_embedding, list) and target_embedding else source_embedding
        if isinstance(chosen_embedding, list) and chosen_embedding:
            vector = [float(item) for item in chosen_embedding if isinstance(item, (int, float))]
            if vector:
                payload["embedding"] = vector
        return payload

    def _accumulate_value(self, left: Any, right: Any) -> Any:
        """同字段累加规则：数字相加、数组并集、文本去重拼接，其余右值优先。"""
        if left in (None, ""):
            return right
        if right in (None, ""):
            return left

        if isinstance(left, (int, float)) and isinstance(right, (int, float)):
            return left + right

        if isinstance(left, list) and isinstance(right, list):
            merged: list[Any] = list(left)
            for item in right:
                if item not in merged:
                    merged.append(item)
            return merged

        left_text = clean_text(left)
        right_text = clean_text(right)
        if left_text and right_text:
            if left_text == right_text:
                return left_text
            return f"{left_text} | {right_text}"
        return right if right not in (None, "") else left

    def _build_relation_payload(
        self,
        row: Mapping[str, Any],
        *,
        target_group_id: str,
        nid_map: Mapping[str, str],
    ) -> RelationPayload | None:
        """将源关系映射为目标关系，并按节点映射重写 source/target nid。"""
        src_map = as_mapping(row.get("src"))
        tgt_map = as_mapping(row.get("tgt"))
        rel_obj = row.get("r")
        rel_map = as_mapping(rel_obj)

        source_nid = clean_text(src_map.get("nid"))
        target_nid = clean_text(tgt_map.get("nid"))
        mapped_source_nid = clean_text(nid_map.get(source_nid))
        mapped_target_nid = clean_text(nid_map.get(target_nid))
        if not mapped_source_nid or not mapped_target_nid:
            return None

        rel_type = clean_text(rel_map.get("rel_type"))
        if not rel_type and hasattr(rel_obj, "type"):
            rel_type = clean_text(getattr(rel_obj, "type"))
        if not rel_type:
            rel_type = DEFAULT_REL_TYPE

        rel_props = extract_properties(rel_map, RELATION_RESERVED_PROP_KEYS)
        meta, clean_props = split_meta_from_props(rel_props)
        direction = clean_text(rel_map.get("direction")) or DEFAULT_DIRECTION
        if direction not in {"forward", "bidirectional"}:
            direction = DEFAULT_DIRECTION

        relation: RelationPayload = {
            "source_nid": mapped_source_nid,
            "target_nid": mapped_target_nid,
            "rel_type": rel_type,
            "direction": direction,  # type: ignore[assignment]
            "description": clean_text(rel_map.get("description")),
            "group_id": target_group_id,
            "properties": clean_props,
            "meta": meta,
        }
        weight = rel_map.get("weight")
        if isinstance(weight, (int, float)):
            relation["weight"] = float(weight)
        return relation

