from __future__ import annotations

from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from core.graph_query_common import build_group_graph_png, parse_limit, run_read_query
from core.types import clean_text


class QueryGroupGraphTool(Tool):
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
ORDER BY coalesce(src.uid, ''), coalesce(tgt.uid, ''), coalesce(r.rel_type, type(r))
LIMIT $limit
"""

    _IMAGE_NODE_LIMIT = 60
    _IMAGE_REL_LIMIT = 120

    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        group_id = clean_text(tool_parameters.get("group_id"))
        database = clean_text(tool_parameters.get("database"))
        if not group_id:
            yield self.create_text_message("❌ group_id 不能为空。")
            return

        try:
            limit = parse_limit(tool_parameters.get("limit"), default=1000, max_value=3000)
            generate_image = bool(tool_parameters.get("generate_image", False))
            nodes_rows = run_read_query(
                self.runtime,
                query=self._NODES_QUERY,
                parameters={"group_id": group_id, "limit": limit},
                database=database,
                limit=limit,
            )
            rels_rows = run_read_query(
                self.runtime,
                query=self._RELS_QUERY,
                parameters={"group_id": group_id, "limit": limit},
                database=database,
                limit=limit,
            )
        except Exception as exc:
            yield self.create_text_message(f"❌ 查询失败：{exc}")
            return

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
            "nodes_count": len(nodes_rows),
            "rels_count": len(rels_rows),
            "nodes": nodes_rows,
            "relations": rels_rows,
            "image_generated": bool(image_png),
            "image_format": "png" if image_png else "",
        }
        summary = f"group_id={group_id} 查询完成，节点 {len(nodes_rows)} 条，关系 {len(rels_rows)} 条。"

        yield self.create_variable_message("nodes_count", len(nodes_rows))
        yield self.create_variable_message("rels_count", len(rels_rows))
        yield self.create_variable_message("results", payload)
        yield self.create_variable_message("summary", summary)
        yield self.create_json_message(payload)

        if image_png:
            yield self.create_variable_message("image_generated", True)
            yield self.create_blob_message(image_png, meta={"mime_type": "image/png", "filename": f"group_{group_id}.png"})

        yield self.create_text_message(f"✅ {summary}")
