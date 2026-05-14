from __future__ import annotations

from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from core.graph_query_common import (
    as_mapping,
    build_group_graph_png,
    parse_bool,
    parse_limit,
    relation_display_name,
    run_read_query,
    strip_embedding_fields,
)
from core.types import clean_text


class QueryGroupGraphTool(Tool):
    _COUNT_NODES_QUERY = """
MATCH (n:KnowledgeNode)
WHERE coalesce(n.group_id, '') = $group_id
RETURN count(n) AS total
"""

    _COUNT_RELS_QUERY = """
MATCH (src:KnowledgeNode)-[r]->(tgt:KnowledgeNode)
WHERE
    (
        coalesce(r.group_id, '') = $group_id
        OR (coalesce(src.group_id, '') = $group_id AND coalesce(tgt.group_id, '') = $group_id)
    )
RETURN count(r) AS total
"""

    _NODES_QUERY = """
MATCH (n:KnowledgeNode)
WHERE coalesce(n.group_id, '') = $group_id
RETURN n
ORDER BY coalesce(n.name, n.uid) ASC
LIMIT $limit
"""

    _RELS_QUERY = """
MATCH (src:KnowledgeNode)-[r]->(tgt:KnowledgeNode)
WHERE
    (
        coalesce(r.group_id, '') = $group_id
        OR (coalesce(src.group_id, '') = $group_id AND coalesce(tgt.group_id, '') = $group_id)
    )
RETURN src, r, tgt
ORDER BY coalesce(src.uid, ''), coalesce(tgt.uid, ''), type(r)
LIMIT $limit
"""

    _IMAGE_NODE_LIMIT = 60
    _IMAGE_REL_LIMIT = 120
    _SAFE_DETAIL_LIMIT = 200

    def _serialize_node(self, node_obj: Any) -> dict[str, Any]:
        node_map = as_mapping(node_obj)
        return {
            "uid": clean_text(node_map.get("uid")),
            "name": clean_text(node_map.get("name")),
            "group_id": clean_text(node_map.get("group_id")),
            "labels": list(node_map.get("labels", []) or []),
            "description": clean_text(node_map.get("description")),
            "properties": node_map.get("properties") if isinstance(node_map.get("properties"), dict) else {},
        }

    def _serialize_relation_row(self, row: dict[str, Any]) -> dict[str, Any]:
        src = self._serialize_node(row.get("src"))
        tgt = self._serialize_node(row.get("tgt"))
        rel = row.get("r")
        rel_map = as_mapping(rel)
        return {
            "source_uid": src.get("uid"),
            "target_uid": tgt.get("uid"),
            "rel_type": relation_display_name(rel),
            "direction": "forward",
            "group_id": clean_text(rel_map.get("group_id")),
            "description": clean_text(rel_map.get("description")),
            "properties": rel_map.get("properties") if isinstance(rel_map.get("properties"), dict) else {},
            "source": src,
            "target": tgt,
        }

    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        group_id = clean_text(tool_parameters.get("group_id"))
        database = clean_text(tool_parameters.get("database"))
        if not group_id:
            yield self.create_text_message("❌ group_id 不能为空。")
            return

        try:
            requested_limit = parse_limit(tool_parameters.get("limit"), default=100, max_value=3000)
            detail_limit = min(requested_limit, self._SAFE_DETAIL_LIMIT)
            generate_image = parse_bool(
                tool_parameters.get("generate_image"),
                default=False,
                field_name="generate_image",
            )
            count_nodes_rows = run_read_query(
                self.runtime,
                query=self._COUNT_NODES_QUERY,
                parameters={"group_id": group_id},
                database=database,
                limit=1,
            )
            count_rels_rows = run_read_query(
                self.runtime,
                query=self._COUNT_RELS_QUERY,
                parameters={"group_id": group_id},
                database=database,
                limit=1,
            )
            nodes_total = int((count_nodes_rows[0] if count_nodes_rows else {}).get("total", 0))
            rels_total = int((count_rels_rows[0] if count_rels_rows else {}).get("total", 0))

            nodes_rows = run_read_query(
                self.runtime,
                query=self._NODES_QUERY,
                parameters={"group_id": group_id, "limit": detail_limit},
                database=database,
                limit=detail_limit,
            )
            rels_rows = run_read_query(
                self.runtime,
                query=self._RELS_QUERY,
                parameters={"group_id": group_id, "limit": detail_limit},
                database=database,
                limit=detail_limit,
            )
        except Exception as exc:
            yield self.create_text_message(f"❌ 查询失败：{exc}")
            return

        nodes_payload = [self._serialize_node(row.get("n")) for row in nodes_rows]
        rels_payload = [self._serialize_relation_row(row) for row in rels_rows]
        nodes_payload = strip_embedding_fields(nodes_payload)
        rels_payload = strip_embedding_fields(rels_payload)

        image_png = b""
        if generate_image:
            try:
                image_png = build_group_graph_png(
                    nodes_rows,
                    rels_rows,
                    node_limit=self._IMAGE_NODE_LIMIT,
                    rel_limit=self._IMAGE_REL_LIMIT,
                )
            except Exception as exc:
                yield self.create_text_message(f"⚠️ 图片生成失败：{exc}")

        payload = {
            "group_id": group_id,
            "nodes_count": len(nodes_payload),
            "rels_count": len(rels_payload),
            "nodes_total": nodes_total,
            "rels_total": rels_total,
            "nodes_truncated": len(nodes_payload) < nodes_total,
            "relations_truncated": len(rels_payload) < rels_total,
            "nodes": nodes_payload,
            "relations": rels_payload,
            "image_generated": bool(image_png),
            "image_format": "png" if image_png else "",
            "request": {
                "group_id": group_id,
                "database": database,
                "limit_requested": requested_limit,
                "limit_applied": detail_limit,
                "generate_image": generate_image,
            },
        }
        summary = (
            f"group_id={group_id} 查询完成，节点总数 {nodes_total} 条（返回 {len(nodes_payload)} 条），"
            f"关系总数 {rels_total} 条（返回 {len(rels_payload)} 条）。"
        )

        yield self.create_variable_message("nodes_count", len(nodes_payload))
        yield self.create_variable_message("rels_count", len(rels_payload))
        yield self.create_variable_message("nodes_total", nodes_total)
        yield self.create_variable_message("rels_total", rels_total)
        yield self.create_variable_message("results", payload)
        yield self.create_variable_message("summary", summary)
        yield self.create_json_message({**payload, "summary": summary})

        if image_png:
            yield self.create_variable_message("image_generated", True)
            yield self.create_blob_message(image_png, meta={"mime_type": "image/png", "filename": f"group_{group_id}.png"})

        yield self.create_text_message(f"✅ {summary}")
