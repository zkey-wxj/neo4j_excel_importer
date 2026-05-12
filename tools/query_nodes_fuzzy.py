from __future__ import annotations

from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from core.graph_query_common import normalize_group_id, parse_limit, run_read_query
from core.types import clean_text


class QueryNodesFuzzyTool(Tool):
    _QUERY = """
MATCH (n:KnowledgeNode)
WHERE (
    toLower(coalesce(n.name, '')) CONTAINS toLower($keyword)
    OR toLower(coalesce(n.description, '')) CONTAINS toLower($keyword)
    OR toLower(coalesce(n.uid, '')) CONTAINS toLower($keyword)
)
AND ($group_id = '' OR coalesce(n.group_id, '') = $group_id)
RETURN n
ORDER BY coalesce(n.name, n.uid) ASC
LIMIT $limit
"""

    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        keyword = clean_text(tool_parameters.get("keyword"))
        database = clean_text(tool_parameters.get("database"))
        group_id = normalize_group_id(tool_parameters.get("group_id"))
        if not keyword:
            yield self.create_text_message("❌ keyword 不能为空。")
            return

        try:
            limit = parse_limit(tool_parameters.get("limit"), default=20, max_value=100)
            rows = run_read_query(
                self.runtime,
                query=self._QUERY,
                parameters={"keyword": keyword, "group_id": group_id, "limit": limit},
                database=database,
                limit=limit,
            )
        except Exception as exc:
            yield self.create_text_message(f"❌ 查询失败：{exc}")
            return

        summary = f"模糊查询完成，关键字“{keyword}”，命中 {len(rows)} 条。"
        yield self.create_variable_message("count", len(rows))
        yield self.create_variable_message("results", rows)
        yield self.create_variable_message("summary", summary)
        yield self.create_json_message({"count": len(rows), "results": rows, "summary": summary})
        yield self.create_text_message(f"✅ {summary}")
