from __future__ import annotations

import logging
from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage
from dify_plugin.config.logger_format import plugin_logger_handler

from core.graph_write_common import write_relations
from core.types import clean_text, get_credentials, normalize_relation

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.addHandler(plugin_logger_handler)


class SaveRelationSingleTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        relation_payload = tool_parameters.get("relation_json")
        group_id = clean_text(tool_parameters.get("group_id")) or clean_text(self.runtime.credentials.get("group_id"))
        logger.info("SaveRelationSingleTool invoked | group_id=%s", group_id)
        if relation_payload is None:
            yield self.create_text_message("❌ relation_json 不能为空。")
            return

        try:
            row = normalize_relation(relation_payload, index=0)
            if group_id and not clean_text(row.get("group_id")):
                row["group_id"] = group_id
            uri, user, pwd = get_credentials(self.runtime)
            count = write_relations(uri, user, pwd, [row], batch_size=1)
        except Exception as exc:
            yield self.create_text_message(f"❌ 保存关系失败：{exc}")
            return

        summary = "关系保存完成，共写入 1 条。"
        yield self.create_variable_message("rels_count", count)
        yield self.create_variable_message("summary", summary)
        yield self.create_text_message(f"✅ {summary}")
