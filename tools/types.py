from __future__ import annotations

import json
import re
from typing import Any, Mapping, TypedDict


class NodePayload(TypedDict):
    nodeId: str
    name: str
    labels: list[str]
    description: str
    groupId: str
    properties: dict[str, Any]


class RelationPayload(TypedDict):
    src: str
    tgt: str
    relType: str
    description: str
    groupId: str
    properties: dict[str, Any]


def clean_text(value: Any) -> str:
    return str(value or "").strip()


def ensure_mapping(value: Any, *, field_name: str) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception as exc:
            raise ValueError(f"{field_name} 必须是对象，字符串无法解析为 JSON：{exc}") from exc
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


def collect_extra_properties(
    row: Mapping[str, Any],
    *,
    excluded_keys: set[str],
) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    for key, raw_value in row.items():
        key_text = clean_text(key)
        if key_text in excluded_keys:
            continue
        normalized_value = normalize_property_value(raw_value)
        if normalized_value == "":
            continue
        properties[key_text] = normalized_value
    return properties


def node_from_excel_row(
    row: Mapping[str, Any],
    *,
    group_id: str = "",
) -> NodePayload:
    row = ensure_mapping(row, field_name="node_row")
    labels = normalize_labels(row.get("node_type"))
    effective_group_id = clean_text(group_id or row.get("group_id"))
    description = clean_text(
        row.get("description")
        or row.get("definition")
        or row.get("说明")
        or row.get("备注")
        or row.get("简介")
    )
    properties = collect_extra_properties(
        row,
        excluded_keys={"NodeID", "name", "node_type", "description", "definition", "group_id", "说明", "备注", "简介"},
    )
    return NodePayload(
        nodeId=clean_text(row.get("NodeID")),
        name=clean_text(row.get("name")),
        labels=labels or ["Node"],
        description=description,
        groupId=effective_group_id,
        properties=properties,
    )


def relation_from_excel_row(
    row: Mapping[str, Any],
    *,
    group_id: str = "",
) -> RelationPayload:
    row = ensure_mapping(row, field_name="relation_row")
    effective_group_id = clean_text(group_id or row.get("group_id"))
    description = clean_text(
        row.get("description")
        or row.get("说明")
        or row.get("备注")
        or row.get("简介")
    )
    properties = collect_extra_properties(
        row,
        excluded_keys={"SourceID", "RelationType", "TargetID", "group_id", "description", "说明", "备注", "简介"},
    )
    return RelationPayload(
        src=clean_text(row.get("SourceID")),
        tgt=clean_text(row.get("TargetID")),
        relType=sanitize_rel_type(clean_text(row.get("RelationType"))),
        description=description,
        groupId=effective_group_id,
        properties=properties,
    )


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

    node_id = clean_text(payload.get("nodeId"))
    if not node_id:
        raise ValueError(f"nodes_json[{index}].nodeId 不能为空。")

    labels = normalize_labels(payload.get("labels"))
    if not labels:
        labels = ["Node"]

    group_id = clean_text(payload.get("groupId") or payload.get("group_id"))
    description = clean_text(
        payload.get("description")
        or payload.get("definition")
        or payload.get("说明")
        or payload.get("备注")
        or payload.get("简介")
    )
    properties = normalize_properties(
        payload.get("properties"),
        field_name=f"nodes_json[{index}].properties",
    )

    fixed_keys = {
        "nodeId",
        "name",
        "labels",
        "description",
        "definition",
        "groupId",
        "group_id",
        "properties",
        "说明",
        "备注",
        "简介",
    }
    for key, raw_value in payload.items():
        if key in fixed_keys:
            continue
        normalized_value = normalize_property_value(raw_value)
        if normalized_value == "":
            continue
        if key not in properties:
            properties[key] = normalized_value

    return NodePayload(
        nodeId=node_id,
        name=clean_text(payload.get("name")),
        labels=labels,
        description=description,
        groupId=group_id,
        properties=properties,
    )


def normalize_relation(payload: Any, *, index: int) -> RelationPayload:
    payload = ensure_mapping(payload, field_name=f"relations_json[{index}]")

    src = clean_text(payload.get("src"))
    tgt = clean_text(payload.get("tgt"))
    rel_type = clean_text(payload.get("relType"))

    if not src:
        raise ValueError(f"relations_json[{index}].src 不能为空。")
    if not tgt:
        raise ValueError(f"relations_json[{index}].tgt 不能为空。")
    if not rel_type:
        raise ValueError(f"relations_json[{index}].relType 不能为空。")

    group_id = clean_text(payload.get("groupId") or payload.get("group_id"))
    description = clean_text(
        payload.get("description")
        or payload.get("说明")
        or payload.get("备注")
        or payload.get("简介")
    )
    properties = normalize_properties(
        payload.get("properties"),
        field_name=f"relations_json[{index}].properties",
    )

    fixed_keys = {"src", "tgt", "relType", "description", "groupId", "group_id", "properties", "说明", "备注", "简介"}
    for key, raw_value in payload.items():
        if key in fixed_keys:
            continue
        normalized_value = normalize_property_value(raw_value)
        if normalized_value == "":
            continue
        if key not in properties:
            properties[key] = normalized_value

    return RelationPayload(
        src=src,
        tgt=tgt,
        relType=sanitize_rel_type(rel_type),
        description=description,
        groupId=group_id,
        properties=properties,
    )
