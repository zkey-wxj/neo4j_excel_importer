from __future__ import annotations

import logging
from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage
from dify_plugin.config.logger_format import plugin_logger_handler

from core.constants import NODE_LABEL
from core.graph_query_common import run_template_query
from core.types import clean_text

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.addHandler(plugin_logger_handler)


class GetGroupLabelsTool(Tool):
    _LABELS_QUERY = f"""
MATCH (n:{NODE_LABEL})
WHERE n.group_id = $group_id
UNWIND labels(n) AS label
WITH DISTINCT label
WHERE label <> '{NODE_LABEL}'
RETURN label
ORDER BY label ASC
"""

    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        group_id = clean_text(tool_parameters.get("group_id")) or clean_text(self.runtime.credentials.get("group_id"))
        database = clean_text(tool_parameters.get("database"))
        logger.info("GetGroupLabelsTool invoked | group_id=%s", group_id)
        if not group_id:
            yield self.create_text_message("❌ group_id 不能为空。")
            return

        try:
            rows = run_template_query(
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

        request_echo = {"group_id": group_id, "database": database}
        summary = f"group_id={group_id} 下共有 {len(labels)} 个不同的 labels。"

        yield self.create_variable_message("group_id", group_id)
        yield self.create_variable_message("labels_count", len(labels))
        yield self.create_variable_message("labels", labels)
        yield self.create_variable_message("request", request_echo)
        yield self.create_variable_message("summary", summary)
        yield self.create_json_message({
            "group_id": group_id,
            "labels_count": len(labels),
            "labels": labels,
            "request": request_echo,
            "summary": summary,
        })
        yield self.create_text_message(f"✅ {summary}")
