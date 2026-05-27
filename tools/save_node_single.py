from __future__ import annotations

import logging
from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage
from dify_plugin.config.logger_format import plugin_logger_handler

from core.embedding_common import build_node_embedding_text, generate_embeddings, has_embedding_model
from core.graph_write_common import write_nodes
from core.types import clean_text, get_credentials, normalize_node

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.addHandler(plugin_logger_handler)


class SaveNodeSingleTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        node_payload = tool_parameters.get("node_json")
        embedding_model = self.runtime.credentials.get("embedding_model")
        group_id = clean_text(tool_parameters.get("group_id")) or clean_text(self.runtime.credentials.get("group_id"))
        logger.info("SaveNodeSingleTool invoked | group_id=%s", group_id)
        if node_payload is None:
            yield self.create_text_message("❌ node_json 不能为空。")
            return

        try:
            row = normalize_node(node_payload, index=0)
            if group_id and not clean_text(row.get("group_id")):
                row["group_id"] = group_id
            if has_embedding_model(embedding_model):
                embedding_text = build_node_embedding_text(row)
                if embedding_text:
                    vectors = generate_embeddings(
                        self.session,
                        model_config=embedding_model,
                        texts=[embedding_text],
                    )
                    if vectors:
                        row["embedding"] = vectors[0]
            uri, user, pwd = get_credentials(self.runtime)
            count = write_nodes(uri, user, pwd, [row], batch_size=1)
        except Exception as exc:
            yield self.create_text_message(f"❌ 保存节点失败：{exc}")
            return

        summary = "节点保存完成，共写入 1 条。"
        yield self.create_variable_message("nodes_count", count)
        yield self.create_variable_message("summary", summary)
        yield self.create_text_message(f"✅ {summary}")
