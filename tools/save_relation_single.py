from __future__ import annotations

from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from tools.graph_write_common import get_credentials, write_relations
from tools.types import parse_relation_json


class SaveRelationSingleTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        relation_json = str(tool_parameters.get("relation_json") or "").strip()
        if not relation_json:
            yield self.create_text_message("❌ relation_json 不能为空。")
            return

        try:
            row = parse_relation_json(relation_json)
            uri, user, pwd = get_credentials(self.runtime)
            count = write_relations(uri, user, pwd, [row], batch_size=1)
        except Exception as exc:
            yield self.create_text_message(f"❌ 保存关系失败：{exc}")
            return

        summary = "关系保存完成，共写入 1 条。"
        yield self.create_variable_message("rels_count", count)
        yield self.create_variable_message("summary", summary)
        yield self.create_text_message(f"✅ {summary}")
