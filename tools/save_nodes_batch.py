from __future__ import annotations

from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from tools.graph_write_common import get_credentials, write_nodes
from tools.types import clean_text, normalize_node


class SaveNodesBatchTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        nodes_payload = tool_parameters.get("nodes_json")
        batch_size = int(tool_parameters.get("batch_size") or 500)
        group_id = clean_text(tool_parameters.get("group_id"))
        if nodes_payload is None:
            yield self.create_text_message("❌ nodes_json 不能为空。")
            return
        if not isinstance(nodes_payload, list):
            yield self.create_text_message("❌ nodes_json 必须是数组。")
            return

        try:
            rows = [normalize_node(item, index=i) for i, item in enumerate(nodes_payload)]
            if group_id:
                for row in rows:
                    if not clean_text(row.get("groupId")):
                        row["groupId"] = group_id
            uri, user, pwd = get_credentials(self.runtime)
            count = write_nodes(uri, user, pwd, rows, batch_size=batch_size)
        except Exception as exc:
            yield self.create_text_message(f"❌ 批量保存节点失败：{exc}")
            return

        summary = f"节点批量保存完成，共写入 {count} 条。"
        yield self.create_variable_message("nodes_count", count)
        yield self.create_variable_message("summary", summary)
        yield self.create_text_message(f"✅ {summary}")
