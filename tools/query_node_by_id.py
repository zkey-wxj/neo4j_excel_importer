from __future__ import annotations

from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from core.graph_query_common import as_mapping, build_node_graph_png, normalize_group_id, parse_bool, run_read_query
from core.types import clean_text


class QueryNodeByIdTool(Tool):
    _NODE_QUERY = """
MATCH (n:KnowledgeNode {uid: $node_id})
WHERE ($group_id = '' OR coalesce(n.group_id, '') = $group_id)
RETURN n
LIMIT 1
"""
    _GRAPH_QUERY = """
MATCH (n:KnowledgeNode {uid: $node_id})-[r]-(m:KnowledgeNode)
WHERE
    ($group_id = '' OR coalesce(n.group_id, '') = $group_id)
    AND ($group_id = '' OR coalesce(m.group_id, '') = $group_id)
RETURN n, r, m
ORDER BY coalesce(m.uid, ''), coalesce(r.rel_type, type(r))
LIMIT $limit
"""
    _IMAGE_NODE_LIMIT = 40
    _IMAGE_REL_LIMIT = 80

    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        node_id = clean_text(tool_parameters.get("node_id"))
        database = clean_text(tool_parameters.get("database"))
        group_id = normalize_group_id(tool_parameters.get("group_id"))
        if not node_id:
            yield self.create_text_message("❌ node_id 不能为空。")
            return

        try:
            generate_image = parse_bool(
                tool_parameters.get("generate_image"),
                default=False,
                field_name="generate_image",
            )
            node_rows = run_read_query(
                self.runtime,
                query=self._NODE_QUERY,
                parameters={"node_id": node_id, "group_id": group_id},
                database=database,
                limit=1,
            )
            if not node_rows:
                summary = f"按 node_id 查询完成，未找到节点：{node_id}。"
                payload = {
                    "count": 0,
                    "node": None,
                    "relations": [],
                    "neighbors": [],
                    "image_generated": False,
                    "image_format": "",
                    "summary": summary,
                    "request": {
                        "node_id": node_id,
                        "group_id": group_id,
                        "database": database,
                        "limit": 100,
                        "generate_image": generate_image,
                    },
                }
                yield self.create_variable_message("count", 0)
                yield self.create_variable_message("results", payload)
                yield self.create_variable_message("summary", summary)
                yield self.create_json_message(payload)
                yield self.create_text_message(f"✅ {summary}")
                return

            limit = 100
            graph_rows = run_read_query(
                self.runtime,
                query=self._GRAPH_QUERY,
                parameters={"node_id": node_id, "group_id": group_id, "limit": limit},
                database=database,
                limit=limit,
            )
        except Exception as exc:
            yield self.create_text_message(f"❌ 查询失败：{exc}")
            return

        center_node = node_rows[0].get("n")
        relations: list[dict[str, Any]] = []
        neighbors_map: dict[str, Any] = {}

        for row in graph_rows:
            relation_obj = row.get("r")
            neighbor_obj = row.get("m")
            if relation_obj is not None and neighbor_obj is not None:
                relations.append({"r": relation_obj, "neighbor": neighbor_obj})
            neighbor_map = as_mapping(neighbor_obj)
            neighbor_id = clean_text(neighbor_map.get("uid"))
            if neighbor_id and neighbor_id not in neighbors_map:
                neighbors_map[neighbor_id] = neighbor_obj

        neighbors = list(neighbors_map.values())
        image_png = b""
        if generate_image:
            try:
                image_png = build_node_graph_png(
                    center_node,
                    graph_rows,
                    node_limit=self._IMAGE_NODE_LIMIT,
                    rel_limit=self._IMAGE_REL_LIMIT,
                )
            except Exception as exc:
                yield self.create_text_message(f"⚠️ 图片生成失败：{exc}")

        payload = {
            "count": len(relations),
            "node": center_node,
            "relations": relations,
            "neighbors": neighbors,
            "image_generated": bool(image_png),
            "image_format": "png" if image_png else "",
            "request": {
                "node_id": node_id,
                "group_id": group_id,
                "database": database,
                "limit": limit,
                "generate_image": generate_image,
            },
        }
        summary = f"按 node_id 查询完成，节点 1 条，关系 {len(relations)} 条，相邻节点 {len(neighbors)} 条。"
        yield self.create_variable_message("count", len(relations))
        yield self.create_variable_message("results", payload)
        yield self.create_variable_message("summary", summary)
        yield self.create_json_message({**payload, "summary": summary})
        if image_png:
            yield self.create_variable_message("image_generated", True)
            yield self.create_blob_message(image_png, meta={"mime_type": "image/png", "filename": f"node_{node_id}.png"})
        yield self.create_text_message(f"✅ {summary}")
