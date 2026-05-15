from __future__ import annotations

from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from core.graph_query_common import run_read_query
from core.types import clean_text


class GetGroupLabelsTool(Tool):
    _LABELS_QUERY = """
MATCH (n:KnowledgeNode)
WHERE coalesce(n.group_id, '') = $group_id
UNWIND labels(n) AS label
WITH DISTINCT label
WHERE label <> 'KnowledgeNode'
RETURN label
ORDER BY label ASC
"""

    def _invoke(self, tool_parameters: dict[str, Any]):
        group_id = clean_text(tool_parameters.get("group_id"))
        database = clean_text(tool_parameters.get("database"))
        if not group_id:
            yield self.create_text_message("❌ group_id 不能为空。")
            return

        try:
            rows = run_read_query(
                self.runtime,
                query=self._LABELS_QUERY,
                parameters={"group_id": group_id},
                database=database,
                limit=1000,
            )
        except Exception as exc:
            yield self.create_text_message(f"❌ 查询失败：{exc}")
            return

        labels = [clean_text(row.get("label")) for row in rows if row.get("label")]
        labels = [lbl for lbl in labels if lbl]

        payload = {
            "group_id": group_id,
            "labels_count": len(labels),
            "labels": labels,
            "request": {"group_id": group_id, "database": database},
        }

        summary = f"group_id={group_id} 下共有 {len(labels)} 个不同的 labels。"

        yield self.create_variable_message("labels_count", len(labels))
        yield self.create_variable_message("labels", labels)
        yield self.create_variable_message("results", payload)
        yield self.create_json_message({**payload, "summary": summary})
        yield self.create_text_message(f"✅ {summary}")
