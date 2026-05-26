from __future__ import annotations

import logging
from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage
from dify_plugin.config.logger_format import plugin_logger_handler

from core.graph_query_common import (
    as_mapping,
    build_node_graph_png,
    normalize_group_id,
    parse_bool,
    run_template_query,
    strip_embedding_fields,
)
from core.types import clean_text

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.addHandler(plugin_logger_handler)


class QueryNodeByIdTool(Tool):
    _NODE_QUERY = """
MATCH (n:KnowledgeNode {nid: $node_id})
WHERE ($group_id = '' OR n.group_id = $group_id)
RETURN n
LIMIT 1
"""
    _GRAPH_QUERY = """
MATCH (n:KnowledgeNode {nid: $node_id})-[r]->(m:KnowledgeNode)
WHERE ($group_id = '' OR r.group_id = $group_id)
RETURN n, r, m
UNION
MATCH (n:KnowledgeNode {nid: $node_id})<-[r]-(m:KnowledgeNode)
WHERE ($group_id = '' OR r.group_id = $group_id)
RETURN n, r, m
LIMIT $limit
"""
    _GRAPH_LIMIT = 100
    _IMAGE_NODE_LIMIT = 40
    _IMAGE_REL_LIMIT = 80

    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        node_id = clean_text(tool_parameters.get("node_id"))
        database = clean_text(tool_parameters.get("database"))
        group_id = normalize_group_id(tool_parameters.get("group_id"))
        logger.info("QueryNodeByIdTool invoked | node_id=%s group_id=%s", node_id, group_id)
        if not node_id:
            yield self.create_text_message("❌ node_id 不能为空。")
            return

        try:
            generate_image = parse_bool(
                tool_parameters.get("generate_image"),
                default=False,
                field_name="generate_image",
            )
            node_rows = run_template_query(
                self.runtime,
                query=self._NODE_QUERY,
                parameters={"node_id": node_id, "group_id": group_id},
                database=database,
                limit=1,
            )
            if not node_rows:
                summary = f"按 node_id 查询完成，未找到节点：{node_id}。"
                request_echo = {
                    "node_id": node_id,
                    "group_id": group_id,
                    "database": database,
                    "limit": 100,
                    "generate_image": generate_image,
                }
                yield self.create_variable_message("count", 0)
                yield self.create_variable_message("node", None)
                yield self.create_variable_message("relations", [])
                yield self.create_variable_message("neighbors", [])
                yield self.create_variable_message("image_generated", False)
                yield self.create_variable_message("image_format", "")
                yield self.create_variable_message("request", request_echo)
                yield self.create_variable_message("summary", summary)
                yield self.create_json_message({
                    "count": 0,
                    "node": None,
                    "relations": [],
                    "neighbors": [],
                    "image_generated": False,
                    "image_format": "",
                    "summary": summary,
                    "request": request_echo,
                })
                yield self.create_text_message(f"✅ {summary}")
                return

            limit = self._GRAPH_LIMIT
            graph_rows = run_template_query(
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
            neighbor_id = clean_text(neighbor_map.get("nid"))
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

        sanitized_center_node = strip_embedding_fields(center_node)
        sanitized_relations = strip_embedding_fields(relations)
        sanitized_neighbors = strip_embedding_fields(neighbors)
        image_generated = bool(image_png)
        image_format = "png" if image_png else ""
        request_echo = {
            "node_id": node_id,
            "group_id": group_id,
            "database": database,
            "limit": limit,
            "generate_image": generate_image,
        }
        summary = f"按 node_id 查询完成，节点 1 条，关系 {len(relations)} 条，相邻节点 {len(neighbors)} 条。"
        yield self.create_variable_message("count", len(relations))
        yield self.create_variable_message("node", sanitized_center_node)
        yield self.create_variable_message("relations", sanitized_relations)
        yield self.create_variable_message("neighbors", sanitized_neighbors)
        yield self.create_variable_message("image_generated", image_generated)
        yield self.create_variable_message("image_format", image_format)
        yield self.create_variable_message("request", request_echo)
        yield self.create_variable_message("summary", summary)
        yield self.create_json_message({
            "count": len(relations),
            "node": sanitized_center_node,
            "relations": sanitized_relations,
            "neighbors": sanitized_neighbors,
            "image_generated": image_generated,
            "image_format": image_format,
            "request": request_echo,
            "summary": summary,
        })
        if image_png:
            yield self.create_blob_message(image_png, meta={"mime_type": "image/png", "filename": f"node_{node_id}.png"})
        yield self.create_text_message(f"✅ {summary}")
