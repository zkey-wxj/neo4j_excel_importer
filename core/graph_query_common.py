from __future__ import annotations

import itertools
import json
from io import BytesIO
from typing import Any, Mapping

from neo4j import GraphDatabase

from core.types import clean_text

READ_QUERY_TYPES = {"r", "s"}


def get_credentials(runtime: Any) -> tuple[str, str, str]:
    uri = clean_text(runtime.credentials.get("neo4j_uri"))
    user = clean_text(runtime.credentials.get("neo4j_user"))
    pwd = clean_text(runtime.credentials.get("neo4j_password"))
    if not uri or not user or not pwd:
        raise ValueError("Neo4j 凭据不完整，请检查 neo4j_uri / neo4j_user / neo4j_password。")
    return uri, user, pwd


def parse_limit(value: Any, *, default: int = 20, max_value: int = 200) -> int:
    if value in (None, ""):
        return default
    try:
        parsed = int(value)
    except Exception as exc:
        raise ValueError("limit 必须是整数。") from exc
    if parsed <= 0:
        raise ValueError("limit 必须大于 0。")
    return min(parsed, max_value)


def parse_bool(value: Any, *, default: bool = False, field_name: str = "value") -> bool:
    """解析布尔参数，支持 bool/int/常见字符串。"""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0

    text = clean_text(value).lower()
    if text in {"", "null", "none"}:
        return default
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    raise ValueError(f"{field_name} 必须是布尔值。")


def parse_json_object(value: Any, *, field_name: str) -> dict[str, Any]:
    """将工具参数解析为 JSON 对象，支持 dict 与 JSON 字符串。"""
    if value in (None, ""):
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception as exc:
            raise ValueError(f"{field_name} 不是合法 JSON：{exc}") from exc
        if not isinstance(parsed, Mapping):
            raise ValueError(f"{field_name} 必须是 JSON 对象。")
        return dict(parsed)
    raise ValueError(f"{field_name} 必须是 JSON 对象或 JSON 字符串。")


def _validate_query_type(
    session: Any,
    *,
    query: str,
    parameters: dict[str, Any],
    allow_write: bool,
) -> str:
    explain_result = session.run(f"EXPLAIN {query}", parameters)
    query_type_raw = explain_result.consume().query_type
    query_type = clean_text(query_type_raw).lower()
    if not allow_write and query_type not in READ_QUERY_TYPES:
        raise ValueError(f"仅允许只读查询，当前 query_type={query_type or 'unknown'}。")
    return query_type or "unknown"


def run_read_query(
    runtime: Any,
    *,
    query: str,
    parameters: dict[str, Any] | None = None,
    database: str = "",
    limit: int = 100,
) -> list[dict[str, Any]]:
    return run_cypher_query(
        runtime,
        query=query,
        parameters=parameters,
        database=database,
        limit=limit,
        allow_write=False,
    )


def run_cypher_query(
    runtime: Any,
    *,
    query: str,
    parameters: dict[str, Any] | None = None,
    database: str = "",
    limit: int = 100,
    allow_write: bool = False,
) -> list[dict[str, Any]]:
    if not clean_text(query):
        raise ValueError("query 不能为空。")

    uri, user, pwd = get_credentials(runtime)
    safe_parameters = parameters or {}
    driver = GraphDatabase.driver(
        uri,
        auth=(user, pwd),
        connection_timeout=30.0,
        max_connection_lifetime=3600,
        user_agent="dify-neo4j-plugin/1.0",
    )
    try:
        session_kwargs: dict[str, Any] = {"fetch_size": min(max(limit, 1), 1000)}
        database_name = clean_text(database)
        if database_name:
            session_kwargs["database"] = database_name

        with driver.session(**session_kwargs) as session:
            _validate_query_type(
                session,
                query=query,
                parameters=safe_parameters,
                allow_write=allow_write,
            )
            result = session.run(query, safe_parameters)
            return [record.data() for record in itertools.islice(result, limit)]
    finally:
        driver.close()


def normalize_group_id(value: Any) -> str:
    return clean_text(value)


def as_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if hasattr(value, "items"):
        try:
            return dict(value.items())
        except Exception:
            return {}
    return {}


def node_display_name(node_obj: Mapping[str, Any]) -> str:
    name = clean_text(node_obj.get("name"))
    node_id = clean_text(node_obj.get("uid"))
    return name or node_id or "Node"


def relation_display_name(rel_obj: Any) -> str:
    rel_type = ""
    if hasattr(rel_obj, "type"):
        rel_type = clean_text(getattr(rel_obj, "type"))
    if not rel_type:
        rel_map = as_mapping(rel_obj)
        rel_type = clean_text(rel_map.get("rel_type"))
    return rel_type or "RELATED"


def build_group_graph_png(
    nodes_rows: list[dict[str, Any]],
    rels_rows: list[dict[str, Any]],
    *,
    node_limit: int = 60,
    rel_limit: int = 120,
) -> bytes:
    nodes_map: dict[str, str] = {}
    for row in nodes_rows:
        node = as_mapping(row.get("n"))
        node_id = clean_text(node.get("uid"))
        if not node_id:
            continue
        if node_id not in nodes_map:
            nodes_map[node_id] = node_display_name(node)

    edges: list[tuple[str, str, str]] = []
    for row in rels_rows[:rel_limit]:
        src = as_mapping(row.get("src"))
        tgt = as_mapping(row.get("tgt"))
        rel = row.get("r")
        src_id = clean_text(src.get("uid"))
        tgt_id = clean_text(tgt.get("uid"))
        if not src_id or not tgt_id:
            continue
        src_name = node_display_name(src)
        tgt_name = node_display_name(tgt)
        nodes_map[src_id] = src_name
        nodes_map[tgt_id] = tgt_name
        edges.append((src_id, tgt_id, relation_display_name(rel)))

    if not edges:
        for node_id, node_name in list(nodes_map.items())[:node_limit]:
            edges.append((node_id, node_id, ""))

    return _render_graph_png(
        nodes_map=nodes_map,
        edges=edges,
        node_limit=node_limit,
        center_node_id="",
        empty_message="当前 group_id 下没有可绘制的节点。",
        width_min=8.0,
        width_factor=0.35,
        width_max=20.0,
        height_min=6.0,
        height_factor=0.28,
        height_max=16.0,
    )


def build_node_graph_png(
    center_node: Any,
    graph_rows: list[dict[str, Any]],
    *,
    node_limit: int = 40,
    rel_limit: int = 80,
) -> bytes:
    center_map = as_mapping(center_node)
    center_id = clean_text(center_map.get("uid"))
    if not center_id:
        raise ValueError("中心节点缺少 uid，无法绘图。")

    nodes_map: dict[str, str] = {center_id: node_display_name(center_map)}
    edges: list[tuple[str, str, str]] = []
    for row in graph_rows[:rel_limit]:
        rel = row.get("r")
        neighbor_map = as_mapping(row.get("m"))
        neighbor_id = clean_text(neighbor_map.get("uid"))
        if not neighbor_id:
            continue
        nodes_map[neighbor_id] = node_display_name(neighbor_map)
        edges.append((center_id, neighbor_id, relation_display_name(rel)))

    return _render_graph_png(
        nodes_map=nodes_map,
        edges=edges,
        node_limit=node_limit,
        center_node_id=center_id,
        empty_message="没有可绘制的节点。",
        width_min=7.0,
        width_factor=0.5,
        width_max=16.0,
        height_min=5.0,
        height_factor=0.35,
        height_max=12.0,
    )


def _render_graph_png(
    *,
    nodes_map: dict[str, str],
    edges: list[tuple[str, str, str]],
    node_limit: int,
    center_node_id: str,
    empty_message: str,
    width_min: float,
    width_factor: float,
    width_max: float,
    height_min: float,
    height_factor: float,
    height_max: float,
) -> bytes:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import networkx as nx

    graph = nx.DiGraph()
    for node_id, node_name in nodes_map.items():
        graph.add_node(node_id, label=node_name)

    edge_count = 0
    for src_id, tgt_id, rel_name in edges:
        if src_id == tgt_id and not rel_name:
            continue
        graph.add_edge(src_id, tgt_id, label=rel_name or "RELATED")
        edge_count += 1

    node_ids = list(graph.nodes())
    if len(node_ids) > node_limit:
        graph = graph.subgraph(node_ids[:node_limit]).copy()
    if graph.number_of_nodes() == 0:
        raise ValueError(empty_message)

    width = min(max(width_min, graph.number_of_nodes() * width_factor), width_max)
    height = min(max(height_min, graph.number_of_nodes() * height_factor), height_max)
    fig, ax = plt.subplots(figsize=(width, height), dpi=140)
    pos = nx.spring_layout(graph, seed=42)
    labels = {node_id: graph.nodes[node_id].get("label", node_id) for node_id in graph.nodes()}
    node_colors = ["#f6ad55" if center_node_id and node_id == center_node_id else "#d8ebff" for node_id in graph.nodes()]

    nx.draw_networkx_nodes(graph, pos, ax=ax, node_size=1250, node_color=node_colors, edgecolors="#2b6cb0")
    nx.draw_networkx_edges(graph, pos, ax=ax, arrows=True, arrowstyle="-|>", arrowsize=16, edge_color="#4a5568", width=1.2)
    nx.draw_networkx_labels(graph, pos, labels=labels, ax=ax, font_size=8, font_color="#1a202c")
    if edge_count <= 40:
        edge_labels = {(u, v): d.get("label", "RELATED") for u, v, d in graph.edges(data=True)}
        nx.draw_networkx_edge_labels(graph, pos, edge_labels=edge_labels, ax=ax, font_size=7, font_color="#2d3748")

    ax.set_axis_off()
    fig.tight_layout(pad=0.4)
    output = BytesIO()
    fig.savefig(output, format="png", bbox_inches="tight")
    plt.close(fig)
    return output.getvalue()
