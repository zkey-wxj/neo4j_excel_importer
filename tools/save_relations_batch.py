from __future__ import annotations

from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from tools.graph_write_common import get_credentials, write_relations
from tools.types import clean_text, normalize_relation


class SaveRelationsBatchTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        relations_payload = tool_parameters.get("relations_json")
        batch_size = int(tool_parameters.get("batch_size") or 500)
        group_id = clean_text(tool_parameters.get("group_id"))
        if relations_payload is None:
            yield self.create_text_message("❌ relations_json 不能为空。")
            return
        if not isinstance(relations_payload, list):
            yield self.create_text_message("❌ relations_json 必须是数组。")
            return

        try:
            rows = [normalize_relation(item, index=i) for i, item in enumerate(relations_payload)]
            if group_id:
                for row in rows:
                    if not clean_text(row.get("groupId")):
                        row["groupId"] = group_id
            uri, user, pwd = get_credentials(self.runtime)
            count = write_relations(uri, user, pwd, rows, batch_size=batch_size)
        except Exception as exc:
            yield self.create_text_message(f"❌ 批量保存关系失败：{exc}")
            return

        summary = f"关系批量保存完成，共写入 {count} 条。"
        yield self.create_variable_message("rels_count", count)
        yield self.create_variable_message("summary", summary)
        yield self.create_text_message(f"✅ {summary}")
