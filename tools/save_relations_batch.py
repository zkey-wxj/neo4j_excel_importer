from __future__ import annotations

from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from tools.graph_write_common import get_credentials, write_relations
from tools.types import parse_relations_json


class SaveRelationsBatchTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        relations_json = str(tool_parameters.get("relations_json") or "").strip()
        batch_size = int(tool_parameters.get("batch_size") or 500)
        if not relations_json:
            yield self.create_text_message("❌ relations_json 不能为空。")
            return

        try:
            rows = parse_relations_json(relations_json)
            uri, user, pwd = get_credentials(self.runtime)
            count = write_relations(uri, user, pwd, rows, batch_size=batch_size)
        except Exception as exc:
            yield self.create_text_message(f"❌ 批量保存关系失败：{exc}")
            return

        summary = f"关系批量保存完成，共写入 {count} 条。"
        yield self.create_variable_message("rels_count", count)
        yield self.create_variable_message("summary", summary)
        yield self.create_text_message(f"✅ {summary}")
