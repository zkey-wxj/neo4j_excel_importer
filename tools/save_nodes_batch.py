from __future__ import annotations

from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from tools.graph_write_common import get_credentials, write_nodes
from tools.types import parse_nodes_json


class SaveNodesBatchTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        nodes_json = str(tool_parameters.get("nodes_json") or "").strip()
        batch_size = int(tool_parameters.get("batch_size") or 500)
        if not nodes_json:
            yield self.create_text_message("❌ nodes_json 不能为空。")
            return

        try:
            rows = parse_nodes_json(nodes_json)
            uri, user, pwd = get_credentials(self.runtime)
            count = write_nodes(uri, user, pwd, rows, batch_size=batch_size)
        except Exception as exc:
            yield self.create_text_message(f"❌ 批量保存节点失败：{exc}")
            return

        summary = f"节点批量保存完成，共写入 {count} 条。"
        yield self.create_variable_message("nodes_count", count)
        yield self.create_variable_message("summary", summary)
        yield self.create_text_message(f"✅ {summary}")
