from __future__ import annotations

from collections.abc import Generator, Mapping
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from core.graph_query_common import as_mapping, run_read_query
from core.graph_write_common import get_credentials, write_nodes, write_relations
from core.types import NodePayload, RelationPayload
from core.types import clean_text


class CopyGroupDataTool(Tool):
    """将一个 group 的节点和关系复制到另一个 group。"""

    _NODE_QUERY = """
MATCH (n:KnowledgeNode)
WHERE coalesce(n.group_id, '') = $group_id
RETURN n
ORDER BY coalesce(n.name, n.uid) ASC
"""

    _REL_QUERY = """
MATCH (src:KnowledgeNode)-[r]->(tgt:KnowledgeNode)
WHERE (
  coalesce(r.group_id, '') = $group_id
  OR (coalesce(src.group_id, '') = $group_id AND coalesce(tgt.group_id, '') = $group_id)
)
RETURN src, r, tgt
ORDER BY coalesce(src.uid, ''), coalesce(tgt.uid, ''), type(r)
"""

    _NODE_RESERVED_PROP_KEYS = {"uid", "name", "description", "group_id", "labels", "properties", "meta", "embedding"}
    _REL_RESERVED_PROP_KEYS = {
        "source_uid",
        "target_uid",
        "rel_type",
        "group_id",
        "direction",
        "description",
        "weight",
        "properties",
        "meta",
    }

    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        """读取 source_group_id 的图数据并复制到 target_group_id。"""
        source_group_id = clean_text(tool_parameters.get("source_group_id"))
        target_group_id = clean_text(tool_parameters.get("target_group_id"))
        batch_size = int(tool_parameters.get("batch_size") or 500)
        database = clean_text(tool_parameters.get("database"))
        uid_prefix = clean_text(tool_parameters.get("uid_prefix")) or f"{target_group_id}::"

        if not source_group_id:
            yield self.create_text_message("❌ source_group_id 不能为空。")
            return
        if not target_group_id:
            yield self.create_text_message("❌ target_group_id 不能为空。")
            return
        if source_group_id == target_group_id:
            yield self.create_text_message("❌ source_group_id 与 target_group_id 不能相同。")
            return
        if not uid_prefix:
            yield self.create_text_message("❌ uid_prefix 不能为空。")
            return

        try:
            node_rows = run_read_query(
                self.runtime,
                query=self._NODE_QUERY,
                parameters={"group_id": source_group_id},
                database=database,
                limit=100000,
            )
            rel_rows = run_read_query(
                self.runtime,
                query=self._REL_QUERY,
                parameters={"group_id": source_group_id},
                database=database,
                limit=200000,
            )
        except Exception as exc:
            yield self.create_text_message(f"❌ 读取源 group 数据失败：{exc}")
            return

        uid_map: dict[str, str] = {}
        node_payloads: list[NodePayload] = []
        for row in node_rows:
            node_payload = self._build_node_payload(
                row.get("n"),
                target_group_id=target_group_id,
                uid_prefix=uid_prefix,
            )
            if not node_payload:
                continue
            source_uid = clean_text(node_payload.get("properties", {}).get("__source_uid"))
            if not source_uid:
                continue
            uid_map[source_uid] = node_payload["uid"]
            node_payload["properties"].pop("__source_uid", None)
            node_payloads.append(node_payload)

        relation_payloads: list[RelationPayload] = []
        for row in rel_rows:
            relation_payload = self._build_relation_payload(
                row,
                target_group_id=target_group_id,
                uid_map=uid_map,
            )
            if not relation_payload:
                continue
            relation_payloads.append(relation_payload)

        if not node_payloads:
            yield self.create_text_message(f"❌ 未在 source_group_id={source_group_id} 下找到可复制节点。")
            return

        try:
            uri, user, pwd = get_credentials(self.runtime)
            nodes_count = write_nodes(
                uri,
                user,
                pwd,
                node_payloads,
                batch_size=batch_size,
            )
            rels_count = write_relations(
                uri,
                user,
                pwd,
                relation_payloads,
                batch_size=batch_size,
            )
        except Exception as exc:
            yield self.create_text_message(f"❌ 复制写入失败：{exc}")
            return

        summary = (
            f"已将 group `{source_group_id}` 复制到 `{target_group_id}`，"
            f"节点 {nodes_count} 条，关系 {rels_count} 条。"
        )
        yield self.create_variable_message("source_group_id", source_group_id)
        yield self.create_variable_message("target_group_id", target_group_id)
        yield self.create_variable_message("uid_prefix", uid_prefix)
        yield self.create_variable_message("nodes_count", nodes_count)
        yield self.create_variable_message("rels_count", rels_count)
        yield self.create_variable_message("summary", summary)
        yield self.create_text_message(f"✅ {summary}")

    def _build_node_payload(
        self,
        node_obj: Any,
        *,
        target_group_id: str,
        uid_prefix: str,
    ) -> NodePayload | None:
        """将源节点映射为目标节点，避免 UID 冲突。"""
        node_map = as_mapping(node_obj)
        source_uid = clean_text(node_map.get("uid"))
        if not source_uid:
            return None
        source_name = clean_text(node_map.get("name")) or source_uid
        labels = self._normalize_labels(node_map.get("labels"))
        properties = self._extract_properties(node_map, self._NODE_RESERVED_PROP_KEYS)
        meta, clean_props = self._split_meta_from_props(properties)
        clean_props["__source_uid"] = source_uid

        payload: NodePayload = {
            "uid": f"{uid_prefix}{source_uid}",
            "name": source_name,
            "labels": labels or ["Node"],
            "description": clean_text(node_map.get("description")),
            "group_id": target_group_id,
            "properties": clean_props,
            "meta": meta,
        }

        embedding = node_map.get("embedding")
        if isinstance(embedding, list) and embedding:
            vector = [float(item) for item in embedding if isinstance(item, (int, float))]
            if vector:
                payload["embedding"] = vector
        return payload

    def _build_relation_payload(
        self,
        row: Mapping[str, Any],
        *,
        target_group_id: str,
        uid_map: Mapping[str, str],
    ) -> RelationPayload | None:
        """将源关系映射为目标关系，source/target uid 复用节点映射。"""
        src_map = as_mapping(row.get("src"))
        tgt_map = as_mapping(row.get("tgt"))
        rel_obj = row.get("r")
        rel_map = as_mapping(rel_obj)

        source_uid = clean_text(src_map.get("uid"))
        target_uid = clean_text(tgt_map.get("uid"))
        mapped_source_uid = clean_text(uid_map.get(source_uid))
        mapped_target_uid = clean_text(uid_map.get(target_uid))
        if not mapped_source_uid or not mapped_target_uid:
            return None

        rel_type = clean_text(rel_map.get("rel_type"))
        if not rel_type and hasattr(rel_obj, "type"):
            rel_type = clean_text(getattr(rel_obj, "type"))
        if not rel_type:
            rel_type = "RELATED"

        rel_props = self._extract_properties(rel_map, self._REL_RESERVED_PROP_KEYS)
        meta, clean_props = self._split_meta_from_props(rel_props)
        direction = clean_text(rel_map.get("direction")) or "forward"
        if direction not in {"forward", "bidirectional"}:
            direction = "forward"

        relation: RelationPayload = {
            "source_uid": mapped_source_uid,
            "target_uid": mapped_target_uid,
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

    def _extract_properties(
        self,
        source: Mapping[str, Any],
        reserved_keys: set[str],
    ) -> dict[str, Any]:
        """提取节点/关系上的业务属性，剔除保留字段。"""
        props: dict[str, Any] = {}

        legacy_props = source.get("properties")
        if isinstance(legacy_props, Mapping):
            for key, value in legacy_props.items():
                normalized_key = clean_text(key)
                if normalized_key and value not in (None, ""):
                    props[normalized_key] = value

        for key, value in source.items():
            normalized_key = clean_text(key)
            if not normalized_key or normalized_key in reserved_keys:
                continue
            if value in (None, ""):
                continue
            props[normalized_key] = value
        return props

    def _split_meta_from_props(self, props: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        """将 meta_* 属性拆回 meta 字段，保持写入格式一致。"""
        meta: dict[str, Any] = {}
        clean_props = dict(props)
        for key in list(clean_props.keys()):
            normalized_key = clean_text(key)
            if not normalized_key.startswith("meta_"):
                continue
            meta_key = clean_text(normalized_key[5:])
            value = clean_props.pop(key, None)
            if not meta_key or value in (None, ""):
                continue
            meta[meta_key] = value
        return meta, clean_props

    def _normalize_labels(self, value: Any) -> list[str]:
        """归一化 labels，确保返回字符串数组。"""
        if isinstance(value, list):
            labels: list[str] = []
            for item in value:
                label = clean_text(item)
                if label and label not in labels:
                    labels.append(label)
            return labels
        label = clean_text(value)
        return [label] if label else []
