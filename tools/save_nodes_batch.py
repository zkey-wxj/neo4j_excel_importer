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


class SaveNodesBatchTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        nodes_payload = tool_parameters.get("nodes_json")
        embedding_model = self.runtime.credentials.get("embedding_model")
        batch_size = int(tool_parameters.get("batch_size") or 500)
        group_id = clean_text(tool_parameters.get("group_id")) or clean_text(self.runtime.credentials.get("group_id"))
        logger.info("SaveNodesBatchTool invoked | count=%s group_id=%s", len(nodes_payload) if isinstance(nodes_payload, list) else "?", group_id)
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
                    if not clean_text(row.get("group_id")):
                        row["group_id"] = group_id
            if has_embedding_model(embedding_model):
                candidate_indexes: list[int] = []
                candidate_texts: list[str] = []
                for index, row in enumerate(rows):
                    embedding_text = build_node_embedding_text(row)
                    if not embedding_text:
                        continue
                    candidate_indexes.append(index)
                    candidate_texts.append(embedding_text)
                if candidate_texts:
                    vectors = generate_embeddings(
                        self.session,
                        model_config=embedding_model,
                        texts=candidate_texts,
                    )
                    if len(vectors) != len(candidate_indexes):
                        raise ValueError("embedding 返回数量与输入节点数量不一致。")
                    for vector_index, row_index in enumerate(candidate_indexes):
                        rows[row_index]["embedding"] = vectors[vector_index]
            uri, user, pwd = get_credentials(self.runtime)
            count = write_nodes(uri, user, pwd, rows, batch_size=batch_size)
        except Exception as exc:
            yield self.create_text_message(f"❌ 批量保存节点失败：{exc}")
            return

        summary = f"节点批量保存完成，共写入 {count} 条。"
        yield self.create_variable_message("nodes_count", count)
        yield self.create_variable_message("summary", summary)
        yield self.create_text_message(f"✅ {summary}")
