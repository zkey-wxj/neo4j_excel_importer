from __future__ import annotations

import logging
from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from dify_plugin.config.logger_format import plugin_logger_handler

from core.graph_query_common import as_mapping, normalize_group_id, parse_limit, run_read_queries, strip_embedding_fields
from core.types import clean_text

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.addHandler(plugin_logger_handler)


class QueryRelationsByNodeTool(Tool):
    _QUERY_FORWARD = """
MATCH (n:KnowledgeNode {uid: $node_id})-[r]->(m:KnowledgeNode)
WHERE
    ($group_id = '' OR r.group_id = $group_id)
    AND (
        $relation_type = ''
        OR type(r) = $relation_type
        OR toLower(coalesce(r.rel_type, '')) = toLower($relation_type)
    )
RETURN n, r, m
"""
    _QUERY_BACKWARD = """
MATCH (n:KnowledgeNode {uid: $node_id})<-[r]-(m:KnowledgeNode)
WHERE
    ($group_id = '' OR r.group_id = $group_id)
    AND (
        $relation_type = ''
        OR type(r) = $relation_type
        OR toLower(coalesce(r.rel_type, '')) = toLower($relation_type)
    )
RETURN n, r, m
"""

    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        node_id = clean_text(tool_parameters.get("node_id"))
        relation_type = clean_text(tool_parameters.get("relation_type"))
        database = clean_text(tool_parameters.get("database"))
        group_id = normalize_group_id(tool_parameters.get("group_id"))
        logger.info("QueryRelationsByNodeTool invoked | node_id=%s group_id=%s", node_id, group_id)
        if not node_id:
            yield self.create_text_message("❌ node_id 不能为空。")
            return

        try:
            limit = parse_limit(tool_parameters.get("limit"), default=20, max_value=100)
            params = {
                "node_id": node_id,
                "group_id": group_id,
                "relation_type": relation_type,
            }
            query_limit = limit + 1
            forward, backward = run_read_queries(
                self.runtime,
                [
                    {"query": self._QUERY_FORWARD, "parameters": params, "limit": query_limit},
                    {"query": self._QUERY_BACKWARD, "parameters": params, "limit": query_limit},
                ],
                database=database,
            )
            seen: set[str] = set()
            rows: list[dict[str, Any]] = []
            for row in forward + backward:
                if len(rows) >= limit:
                    break
                r = row.get("r")
                m = row.get("m")
                r_id = str(getattr(r, "element_id", None) or getattr(r, "id", ""))
                m_uid = clean_text(as_mapping(m).get("uid"))
                dedup_key = f"{r_id}:{m_uid}"
                if dedup_key not in seen:
                    seen.add(dedup_key)
                    rows.append(row)
        except Exception as exc:
            yield self.create_text_message(f"❌ 查询失败：{exc}")
            return

        sanitized_rows = strip_embedding_fields(rows)
        summary = f"关系查询完成，node_id={node_id}，命中 {len(rows)} 条。"
        request_echo = {
            "node_id": node_id,
            "relation_type": relation_type,
            "group_id": group_id,
            "database": database,
            "limit": limit,
        }
        yield self.create_variable_message("count", len(rows))
        yield self.create_variable_message("results", sanitized_rows)
        yield self.create_variable_message("summary", summary)
        yield self.create_variable_message("request", request_echo)
        yield self.create_json_message({
            "count": len(rows),
            "results": sanitized_rows,
            "summary": summary,
            "request": request_echo,
        })
        yield self.create_text_message(f"✅ {summary}")
