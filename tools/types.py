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


def sanitize_label(text: str) -> str:
    value = clean_text(text)
    value = re.sub(r"[/\\\-\s]+", "_", value)
    return value or "Node"


def sanitize_rel_type(text: str) -> str:
    value = clean_text(text)
    value = re.sub(r"[/\\\-\s]+", "_", value)
    return value or "RELATED_TO"


def node_from_excel_row(row: Mapping[str, Any]) -> NodePayload:
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
    if not isinstance(payload, Mapping):
        raise ValueError(f"nodes_json[{index}] 必须是 JSON 对象。")

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
    if not isinstance(payload, Mapping):
        raise ValueError(f"relations_json[{index}] 必须是 JSON 对象。")

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
