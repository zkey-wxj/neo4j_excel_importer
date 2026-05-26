from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Mapping
from typing_extensions import NotRequired, TypedDict

from core.constants import (
    DEFAULT_DIRECTION,
    DEFAULT_NODE_LABEL,
    DEFAULT_REL_TYPE,
    Direction,
)


class GraphMeta(TypedDict):
    created_at: str
    updated_at: str
    source: NotRequired[str]
    version: NotRequired[int]


class NodePayload(TypedDict):
    nid: str
    name: str
    labels: list[str]
    description: NotRequired[str]
    group_id: str
    properties: dict[str, Any]
    embedding: NotRequired[list[float]]
    meta: GraphMeta


class RelationPayload(TypedDict):
    source_nid: str
    target_nid: str
    rel_type: str
    direction: Direction
    description: NotRequired[str]
    weight: NotRequired[float]
    group_id: str
    properties: dict[str, Any]
    meta: GraphMeta


NODE_PAYLOAD_FIELDS = frozenset(NodePayload.__annotations__.keys())
RELATION_PAYLOAD_FIELDS = frozenset(RelationPayload.__annotations__.keys())


def get_credentials(runtime: Any) -> tuple[str, str, str]:
    """从 runtime.credentials 提取并校验 Neo4j 连接信息。"""
    uri = clean_text(runtime.credentials.get("neo4j_uri"))
    user = clean_text(runtime.credentials.get("neo4j_user"))
    pwd = clean_text(runtime.credentials.get("neo4j_password"))
    if not uri or not user or not pwd:
        raise ValueError("Neo4j 凭据不完整，请检查 neo4j_uri / neo4j_user / neo4j_password。")
    return uri, user, pwd


def extract_properties(
    source: Mapping[str, Any],
    reserved_keys: frozenset[str],
) -> dict[str, Any]:
    """提取节点/关系上的业务属性，剔除保留字段。"""
    props: dict[str, Any] = {}

    legacy_props = source.get("properties")
    if isinstance(legacy_props, Mapping):
        for key, value in legacy_props.items():
            normalized_key = clean_text(key)
            if normalized_key and value not in (None, ""):
                props[normalized_key] = value

    for key, value in source.items():
        normalized_key = clean_text(key)
        if not normalized_key or normalized_key in reserved_keys:
            continue
        if value in (None, ""):
            continue
        props[normalized_key] = value
    return props


def split_meta_from_props(props: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """将 meta_* 属性拆回 meta 字段，保持写入格式一致。"""
    meta: dict[str, Any] = {}
    clean_props = dict(props)
    for key in list(clean_props.keys()):
        normalized_key = clean_text(key)
        if not normalized_key.startswith("meta_"):
            continue
        meta_key = clean_text(normalized_key[5:])
        value = clean_props.pop(key, None)
        if not meta_key or value in (None, ""):
            continue
        meta[meta_key] = value
    return meta, clean_props


def clean_text(value: Any) -> str:
    return str(value or "").strip()


def ensure_mapping(value: Any, *, field_name: str) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception as exc:
            raise ValueError(
                f"{field_name} 必须是对象，字符串无法解析为 JSON：{exc}"
            ) from exc
        if isinstance(parsed, Mapping):
            return parsed
    raise ValueError(f"{field_name} 必须是 JSON 对象。")


def sanitize_label(text: str) -> str:
    value = clean_text(text)
    value = re.sub(r"[/\\\-\s]+", "_", value)
    return value or "Node"


def sanitize_rel_type(text: str) -> str:
    value = clean_text(text)
    value = re.sub(r"[/\\\-\s]+", "_", value)
    return value or "RELATED_TO"


def utc_now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def normalize_labels(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        labels: list[str] = []
        for item in value:
            label = sanitize_label(item)
            if label and label not in labels:
                labels.append(label)
        return labels

    text = clean_text(value)
    if not text:
        return []

    if text.startswith("[") and text.endswith("]"):
        try:
            parsed = json.loads(text)
        except Exception:
            parsed = None
        if isinstance(parsed, list):
            labels = normalize_labels(parsed)
            if labels:
                return labels

    return [sanitize_label(text)]


def normalize_property_value(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, list):
        normalized_list: list[Any] = []
        for item in value:
            if isinstance(item, str):
                normalized_list.append(item.strip())
            elif isinstance(item, (bool, int, float)):
                normalized_list.append(item)
            else:
                normalized_list.append(clean_text(item))
        return normalized_list
    return clean_text(value)


def normalize_embedding(
    value: Any,
    *,
    field_name: str,
) -> list[float]:
    if value in (None, ""):
        return []

    candidate = value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            candidate = json.loads(text)
        except Exception as exc:
            raise ValueError(f"{field_name} 不是合法向量 JSON：{exc}") from exc

    if not isinstance(candidate, list):
        raise ValueError(f"{field_name} 必须是 number 数组。")
    if not candidate:
        return []

    result: list[float] = []
    for index, item in enumerate(candidate):
        if not isinstance(item, (int, float)):
            raise ValueError(f"{field_name}[{index}] 必须是数字。")
        result.append(float(item))
    return result


def normalize_properties(
    value: Any,
    *,
    field_name: str,
) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} 必须是 JSON 对象。")

    result: dict[str, Any] = {}
    for key, raw_value in value.items():
        property_key = clean_text(key)
        if not property_key:
            continue
        normalized_value = normalize_property_value(raw_value)
        if normalized_value == "":
            continue
        result[property_key] = normalized_value
    return result


def normalize_meta(value: Any, *, field_name: str) -> GraphMeta:
    if value in (None, ""):
        now = utc_now_iso()
        payload: Mapping[str, Any] = {"created_at": now, "updated_at": now}
    else:
        payload = ensure_mapping(value, field_name=field_name)

    created_at = clean_text(payload.get("created_at")) or utc_now_iso()
    updated_at = clean_text(payload.get("updated_at")) or created_at

    meta: GraphMeta = {
        "created_at": created_at,
        "updated_at": updated_at,
    }
    source = clean_text(payload.get("source"))
    if source:
        meta["source"] = source
    version = payload.get("version")
    if isinstance(version, int):
        meta["version"] = version
    return meta


def parse_nodes_json(nodes_json: str) -> list[NodePayload]:
    payload = json.loads(nodes_json)
    if not isinstance(payload, list):
        raise ValueError("nodes_json 必须是 JSON 数组。")
    return [normalize_node(item, index=i) for i, item in enumerate(payload)]


def parse_node_json(node_json: str) -> NodePayload:
    payload = json.loads(node_json)
    return normalize_node(payload, index=0)


def parse_relations_json(relations_json: str) -> list[RelationPayload]:
    payload = json.loads(relations_json)
    if not isinstance(payload, list):
        raise ValueError("relations_json 必须是 JSON 数组。")
    return [normalize_relation(item, index=i) for i, item in enumerate(payload)]


def parse_relation_json(relation_json: str) -> RelationPayload:
    payload = json.loads(relation_json)
    return normalize_relation(payload, index=0)


def normalize_node(payload: Any, *, index: int) -> NodePayload:
    payload = ensure_mapping(payload, field_name=f"nodes_json[{index}]")

    nid = clean_text(payload.get("nid"))
    if not nid:
        raise ValueError(f"nodes_json[{index}].nid 不能为空。")

    labels = normalize_labels(payload.get("labels"))
    if not labels:
        labels = ["Node"]

    group_id = clean_text(payload.get("group_id"))
    description = clean_text(payload.get("description"))
    properties = normalize_properties(
        payload.get("properties"),
        field_name=f"nodes_json[{index}].properties",
    )
    embedding = normalize_embedding(
        payload.get("embedding"),
        field_name=f"nodes_json[{index}].embedding",
    )
    meta = normalize_meta(payload.get("meta"), field_name=f"nodes_json[{index}].meta")

    node: NodePayload = {
        "nid": nid,
        "name": clean_text(payload.get("name")),
        "labels": labels,
        "group_id": group_id,
        "properties": properties,
        "meta": meta,
    }
    if description:
        node["description"] = description
    if embedding:
        node["embedding"] = embedding
    return node


def normalize_relation(payload: Any, *, index: int) -> RelationPayload:
    payload = ensure_mapping(payload, field_name=f"relations_json[{index}]")

    source_nid = clean_text(payload.get("source_nid"))
    target_nid = clean_text(payload.get("target_nid"))
    rel_type = clean_text(payload.get("rel_type"))
    if not source_nid:
        raise ValueError(f"relations_json[{index}].source_nid 不能为空。")
    if not target_nid:
        raise ValueError(f"relations_json[{index}].target_nid 不能为空。")
    if not rel_type:
        raise ValueError(f"relations_json[{index}].rel_type 不能为空。")

    direction = clean_text(payload.get("direction")) or DEFAULT_DIRECTION
    if direction not in {"forward", "bidirectional"}:
        raise ValueError(
            f"relations_json[{index}].direction 非法，必须是 forward 或 bidirectional。"
        )

    group_id = clean_text(payload.get("group_id"))
    description = clean_text(payload.get("description"))
    properties = normalize_properties(
        payload.get("properties"),
        field_name=f"relations_json[{index}].properties",
    )
    meta = normalize_meta(
        payload.get("meta"), field_name=f"relations_json[{index}].meta"
    )

    relation: RelationPayload = {
        "source_nid": source_nid,
        "target_nid": target_nid,
        "rel_type": rel_type,
        "direction": direction,  # type: ignore[assignment]
        "group_id": group_id,
        "properties": properties,
        "meta": meta,
    }
    if description:
        relation["description"] = description
    weight = payload.get("weight")
    if isinstance(weight, (int, float)):
        relation["weight"] = float(weight)
    return relation
