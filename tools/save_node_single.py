from __future__ import annotations

from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from tools.graph_write_common import get_credentials, write_nodes
from tools.types import parse_node_json


class SaveNodeSingleTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        node_json = str(tool_parameters.get("node_json") or "").strip()
        if not node_json:
            yield self.create_text_message("❌ node_json 不能为空。")
            return

        try:
            row = parse_node_json(node_json)
            uri, user, pwd = get_credentials(self.runtime)
            count = write_nodes(uri, user, pwd, [row], batch_size=1)
        except Exception as exc:
            yield self.create_text_message(f"❌ 保存节点失败：{exc}")
            return

        summary = "节点保存完成，共写入 1 条。"
        yield self.create_variable_message("nodes_count", count)
        yield self.create_variable_message("summary", summary)
        yield self.create_text_message(f"✅ {summary}")
