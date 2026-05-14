from __future__ import annotations

from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from core.graph_query_common import normalize_group_id, parse_limit, run_read_query
from core.types import clean_text


class QueryRelationsByNodeTool(Tool):
    _QUERY = """
MATCH (n:KnowledgeNode {uid: $node_id})-[r]-(m:KnowledgeNode)
WHERE
    ($group_id = '' OR coalesce(n.group_id, '') = $group_id)
    AND ($group_id = '' OR coalesce(m.group_id, '') = $group_id)
    AND (
        $relation_type = ''
        OR type(r) = $relation_type
        OR toLower(coalesce(r.rel_type, '')) = toLower($relation_type)
    )
RETURN n, r, m
ORDER BY coalesce(m.uid, ''), coalesce(r.rel_type, type(r))
LIMIT $limit
"""

    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        node_id = clean_text(tool_parameters.get("node_id"))
        relation_type = clean_text(tool_parameters.get("relation_type"))
        database = clean_text(tool_parameters.get("database"))
        group_id = normalize_group_id(tool_parameters.get("group_id"))
        if not node_id:
            yield self.create_text_message("❌ node_id 不能为空。")
            return

        try:
            limit = parse_limit(tool_parameters.get("limit"), default=20, max_value=100)
            rows = run_read_query(
                self.runtime,
                query=self._QUERY,
                parameters={
                    "node_id": node_id,
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

        summary = f"关系查询完成，node_id={node_id}，命中 {len(rows)} 条。"
        payload = {
            "count": len(rows),
            "results": rows,
            "summary": summary,
            "request": {
                "node_id": node_id,
                "relation_type": relation_type,
                "group_id": group_id,
                "database": database,
                "limit": limit,
            },
        }
        yield self.create_variable_message("count", len(rows))
        yield self.create_variable_message("results", rows)
        yield self.create_variable_message("summary", summary)
        yield self.create_json_message(payload)
        yield self.create_text_message(f"✅ {summary}")
