from __future__ import annotations

import logging
from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage
from dify_plugin.config.logger_format import plugin_logger_handler

from core.graph_query_common import normalize_group_id, parse_limit, run_template_query, strip_embedding_fields
from core.types import clean_text

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.addHandler(plugin_logger_handler)


class QueryRelationsBetweenNodesTool(Tool):
    _QUERY = """
MATCH (src:KnowledgeNode {nid: $source_nid})-[r]->(tgt:KnowledgeNode {nid: $target_nid})
WHERE
    ($group_id = '' OR r.group_id = $group_id)
    AND (
        $relation_type = ''
        OR type(r) = $relation_type
        OR toLower(coalesce(r.rel_type, '')) = toLower($relation_type)
    )
RETURN src, r, tgt
ORDER BY coalesce(r.rel_type, type(r))
LIMIT $limit
"""

    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        source_nid = clean_text(tool_parameters.get("source_nid"))
        target_nid = clean_text(tool_parameters.get("target_nid"))
        relation_type = clean_text(tool_parameters.get("relation_type"))
        database = clean_text(tool_parameters.get("database"))
        group_id = normalize_group_id(tool_parameters.get("group_id"))
        logger.info("QueryRelationsBetweenNodesTool invoked | src=%s tgt=%s", source_nid, target_nid)
        if not source_nid or not target_nid:
            yield self.create_text_message("❌ source_nid 与 target_nid 不能为空。")
            return

        try:
            limit = parse_limit(tool_parameters.get("limit"), default=20, max_value=100)
            rows = run_template_query(
                self.runtime,
                query=self._QUERY,
                parameters={
                    "source_nid": source_nid,
                    "target_nid": target_nid,
                    "group_id": group_id,
                    "relation_type": relation_type,
                    "limit": limit,
                },
                database=database,
                limit=limit,
            )
        except Exception as exc:
            yield self.create_text_message(f"❌ 查询失败：{exc}")
            return

        sanitized_rows = strip_embedding_fields(rows)
        summary = f"节点间关系查询完成，source_nid={source_nid}，target_nid={target_nid}，命中 {len(rows)} 条。"
        request_echo = {
            "source_nid": source_nid,
            "target_nid": target_nid,
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
