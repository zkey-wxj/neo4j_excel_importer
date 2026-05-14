from __future__ import annotations

from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from core.graph_query_common import normalize_group_id, parse_limit, run_read_query
from core.types import clean_text


class QueryRelationsBetweenNodesTool(Tool):
    _QUERY = """
MATCH (src:KnowledgeNode {uid: $source_uid})-[r]->(tgt:KnowledgeNode {uid: $target_uid})
WHERE
    ($group_id = '' OR coalesce(src.group_id, '') = $group_id)
    AND ($group_id = '' OR coalesce(tgt.group_id, '') = $group_id)
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
        source_uid = clean_text(tool_parameters.get("source_uid"))
        target_uid = clean_text(tool_parameters.get("target_uid"))
        relation_type = clean_text(tool_parameters.get("relation_type"))
        database = clean_text(tool_parameters.get("database"))
        group_id = normalize_group_id(tool_parameters.get("group_id"))
        if not source_uid or not target_uid:
            yield self.create_text_message("❌ source_uid 与 target_uid 不能为空。")
            return

        try:
            limit = parse_limit(tool_parameters.get("limit"), default=20, max_value=100)
            rows = run_read_query(
                self.runtime,
                query=self._QUERY,
                parameters={
                    "source_uid": source_uid,
                    "target_uid": target_uid,
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

        summary = f"节点间关系查询完成，source_uid={source_uid}，target_uid={target_uid}，命中 {len(rows)} 条。"
        payload = {
            "count": len(rows),
            "results": rows,
            "summary": summary,
            "request": {
                "source_uid": source_uid,
                "target_uid": target_uid,
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
