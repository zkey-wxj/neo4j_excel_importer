from __future__ import annotations

from collections.abc import Mapping
import time
from typing import Any
from dify_plugin.core.runtime import Session
from dify_plugin.entities.model.text_embedding import (
    TextEmbeddingModelConfig,
    TextEmbeddingResult,
)

from core.types import NodePayload, clean_text


def has_embedding_model(model_config: Any) -> bool:
    config = _as_mapping(model_config)
    return bool(clean_text(config.get("provider")) and clean_text(config.get("model")))


def build_node_embedding_text(node: NodePayload) -> str:
    parts: list[str] = []

    nid = clean_text(node.get("nid"))
    name = clean_text(node.get("name"))
    description = clean_text(node.get("description"))
    labels = node.get("labels") or []
    properties = node.get("properties") or {}

    if nid:
        parts.append(nid)
    if name:
        parts.append(name)
    if description:
        parts.append(description)
    if labels:
        parts.append(", ".join(str(item).strip() for item in labels if str(item).strip()))
    if isinstance(properties, Mapping) and properties:
        for key in sorted(properties.keys()):
            key_text = clean_text(key)
            value_text = clean_text(properties.get(key))
            if not key_text or not value_text:
                continue
            parts.append(value_text)

    return "；".join(part for part in parts if part).strip()


def generate_embeddings(
    session: Session,
    *,
    model_config: Any,
    texts: list[str],
) -> list[list[float]]:
    if not has_embedding_model(model_config):
        raise ValueError("embedding_model 未配置 provider/model，无法生成向量。")
    if not texts:
        return []

    normalized_model_config = _to_text_embedding_model_config(model_config)
    result = _invoke_text_embedding_with_retry(
        session=session,
        model_config=normalized_model_config,
        texts=texts,
    )
    if not isinstance(result, TextEmbeddingResult):
        raise ValueError("text_embedding 返回类型错误，预期 TextEmbeddingResult。")
    embeddings = result.embeddings
    if not isinstance(embeddings, list):
        raise ValueError("text_embedding 返回结构缺少 embeddings。")

    normalized: list[list[float]] = []
    for row_index, vector in enumerate(embeddings):
        if not isinstance(vector, list) or not vector:
            raise ValueError(f"第 {row_index} 条 embedding 为空或格式错误。")
        normalized_vector: list[float] = []
        for value in vector:
            if not isinstance(value, (int, float)):
                raise ValueError(f"第 {row_index} 条 embedding 含非数字元素。")
            normalized_vector.append(float(value))
        normalized.append(normalized_vector)
    return normalized


def _invoke_text_embedding_with_retry(
    *,
    session: Session,
    model_config: TextEmbeddingModelConfig,
    texts: list[str],
    max_attempts: int = 3,
    initial_delay_seconds: float = 1.0,
) -> TextEmbeddingResult:
    """
    调用 embedding 模型并对临时性失败做有限重试，避免偶发 5xx 直接中断导入。
    """
    delay_seconds = initial_delay_seconds
    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return session.model.text_embedding.invoke(
                model_config=model_config,
                texts=texts,
            )
        except Exception as exc:
            last_error = exc
            if attempt >= max_attempts or not _is_retryable_embedding_error(exc):
                raise
            time.sleep(delay_seconds)
            delay_seconds *= 2
    if last_error is not None:
        raise last_error
    raise RuntimeError("text_embedding 调用失败，且未捕获具体异常。")


def _is_retryable_embedding_error(exc: Exception) -> bool:
    """
    判断 embedding 失败是否属于可重试的临时错误。
    """
    message = str(exc).lower()
    retryable_markers = (
        "500",
        "502",
        "503",
        "504",
        "request failed",
        "timeout",
        "timed out",
        "temporarily unavailable",
        "connection reset",
    )
    return any(marker in message for marker in retryable_markers)


def _as_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if hasattr(value, "model_dump"):
        try:
            dumped = value.model_dump()
            if isinstance(dumped, Mapping):
                return dumped
        except Exception:
            return {}
    return {}


def _to_text_embedding_model_config(model_config: Any) -> TextEmbeddingModelConfig:
    if isinstance(model_config, TextEmbeddingModelConfig):
        return model_config
    config = _as_mapping(model_config)
    if not config:
        raise ValueError("embedding_model 必须是 model-selector 返回的模型配置对象。")
    return TextEmbeddingModelConfig.model_validate(dict(config))
