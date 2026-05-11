from __future__ import annotations

import json
import re
from typing import Any, Mapping, TypedDict


class NodePayload(TypedDict):
    nodeId: str
    name: str
    nodeType: str
    label: str
    definition: str
    level: str
    gradeRange: str
    keywords: str
    teachingTip: str


class RelationPayload(TypedDict):
    src: str
    tgt: str
    relType: str
    desc: str


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


def node_from_excel_row(row: Mapping[str, Any]) -> NodePayload:
    row = ensure_mapping(row, field_name="node_row")
    node_type = clean_text(row.get("node_type"))
    return NodePayload(
        nodeId=clean_text(row.get("NodeID")),
        name=clean_text(row.get("name")),
        nodeType=node_type,
        label=sanitize_label(node_type) if node_type else "Node",
        definition=clean_text(row.get("definition")),
        level=clean_text(row.get("level")),
        gradeRange=clean_text(row.get("grade_range")),
        keywords=clean_text(row.get("keywords")),
        teachingTip=clean_text(row.get("teaching_tip")),
    )


def relation_from_excel_row(row: Mapping[str, Any]) -> RelationPayload:
    row = ensure_mapping(row, field_name="relation_row")
    return RelationPayload(
        src=clean_text(row.get("SourceID")),
        tgt=clean_text(row.get("TargetID")),
        relType=sanitize_rel_type(clean_text(row.get("RelationType"))),
        desc=clean_text(row.get("description")),
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

    node_type = clean_text(payload.get("nodeType"))
    label = clean_text(payload.get("label")) or (sanitize_label(node_type) if node_type else "Node")

    return NodePayload(
        nodeId=node_id,
        name=clean_text(payload.get("name")),
        nodeType=node_type,
        label=label,
        definition=clean_text(payload.get("definition")),
        level=clean_text(payload.get("level")),
        gradeRange=clean_text(payload.get("gradeRange")),
        keywords=clean_text(payload.get("keywords")),
        teachingTip=clean_text(payload.get("teachingTip")),
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

    return RelationPayload(
        src=src,
        tgt=tgt,
        relType=sanitize_rel_type(rel_type),
        desc=clean_text(payload.get("desc")),
    )
