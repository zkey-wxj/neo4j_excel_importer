from __future__ import annotations

import itertools
import json
import threading
from dataclasses import dataclass
from io import BytesIO
from typing import Any, Mapping

from neo4j import GraphDatabase

from core.types import clean_text, get_credentials

READ_QUERY_TYPES = {"r", "s"}

# ── Driver 缓存 ────────────────────────────────────────────────────────────
_DRIVER_CACHE: dict[tuple[str, str], Any] = {}
_DRIVER_CACHE_LOCK = threading.Lock()


def get_driver(uri: str, user: str, pwd: str) -> Any:
    """获取或创建缓存的 Neo4j Driver 实例，按 (uri, user) 复用。"""
    cache_key = (uri, user)
    driver = _DRIVER_CACHE.get(cache_key)
    if driver is not None:
        return driver
    with _DRIVER_CACHE_LOCK:
        driver = _DRIVER_CACHE.get(cache_key)
        if driver is not None:
            return driver
        driver = GraphDatabase.driver(
            uri,
            auth=(user, pwd),
            connection_timeout=30.0,
            max_connection_lifetime=3600,
            user_agent="dify-neo4j-plugin/1.0",
        )
        _DRIVER_CACHE[cache_key] = driver
        return driver


def clear_driver_cache() -> None:
    """关闭并清空所有缓存的 Driver。"""
    with _DRIVER_CACHE_LOCK:
        for driver in _DRIVER_CACHE.values():
            try:
                driver.close()
            except Exception:
                pass
        _DRIVER_CACHE.clear()


# ── 索引初始化标记 ─────────────────────────────────────────────────────────
_GROUP_ID_INDEX_CREATED: set[str] = set()
_GROUP_ID_PROP_MIGRATED: set[str] = set()
_FULLTEXT_INDEX_CREATED: set[str] = set()
_INIT_LOCK = threading.Lock()


def _ensure_group_id_index(session: Any, database: str) -> None:
    """确保 group_id 属性索引存在（节点 + 关系），每个数据库仅首次执行。"""
    if database in _GROUP_ID_INDEX_CREATED:
        return
    with _INIT_LOCK:
        if database in _GROUP_ID_INDEX_CREATED:
            return
        try:
            session.run(
                "CREATE INDEX IF NOT EXISTS FOR (n:KnowledgeNode) ON (n.group_id)"
            ).consume()
            session.run(
                "CREATE INDEX IF NOT EXISTS FOR ()-[r:RELATED]-() ON (r.group_id)"
            ).consume()
        except Exception:
            pass
        _GROUP_ID_INDEX_CREATED.add(database)


def _ensure_group_id_prop(session: Any, database: str) -> None:
    """为缺少 group_id 属性的节点/关系补上空字符串，每个数据库仅执行一次。"""
    if database in _GROUP_ID_PROP_MIGRATED:
        return
    with _INIT_LOCK:
        if database in _GROUP_ID_PROP_MIGRATED:
            return
        try:
            session.run(
                "MATCH (n:KnowledgeNode) WHERE NOT exists(n.group_id) "
                "SET n.group_id = ''"
            ).consume()
            session.run(
                "MATCH ()-[r:RELATED]-() WHERE NOT exists(r.group_id) "
                "SET r.group_id = ''"
            ).consume()
        except Exception:
            pass
        _GROUP_ID_PROP_MIGRATED.add(database)


def _ensure_fulltext_index(session: Any, database: str) -> bool:
    """确保全文索引存在，返回是否可用。每个数据库仅首次执行。"""
    if database in _FULLTEXT_INDEX_CREATED:
        return True
    with _INIT_LOCK:
        if database in _FULLTEXT_INDEX_CREATED:
            return True
        try:
            session.run(
                "CREATE FULLTEXT INDEX node_fulltext IF NOT EXISTS "
                "FOR (n:KnowledgeNode) ON EACH [n.name, n.description, n.uid]"
            ).consume()
            _FULLTEXT_INDEX_CREATED.add(database)
            return True
        except Exception:
            return False


# ── 参数解析 ────────────────────────────────────────────────────────────────

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


# ── 查询类型校验 ────────────────────────────────────────────────────────────

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


# ── 内部查询执行（跳过 EXPLAIN，供固定模板使用） ───────────────────────────

def run_template_query(
    runtime: Any,
    *,
    query: str,
    parameters: dict[str, Any] | None = None,
    database: str = "",
    limit: int = 100,
) -> list[dict[str, Any]]:
    """执行固定模板查询，跳过 EXPLAIN 校验，复用缓存 Driver。"""
    uri, user, pwd = get_credentials(runtime)
    safe_parameters = parameters or {}
    driver = get_driver(uri, user, pwd)

    session_kwargs: dict[str, Any] = {"fetch_size": min(max(limit, 1), 1000)}
    database_name = clean_text(database)
    if database_name:
        session_kwargs["database"] = database_name

    with driver.session(**session_kwargs) as session:
        _ensure_group_id_index(session, database_name)
        _ensure_group_id_prop(session, database_name)
        result = session.run(query, safe_parameters)  # type: ignore[arg-type]
        return [record.data() for record in itertools.islice(result, limit)]


# ── 公开查询接口 ────────────────────────────────────────────────────────────

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
    """执行自定义 Cypher 查询（含 EXPLAIN 校验），复用缓存 Driver。"""
    if not clean_text(query):
        raise ValueError("query 不能为空。")

    uri, user, pwd = get_credentials(runtime)
    safe_parameters = parameters or {}
    driver = get_driver(uri, user, pwd)

    session_kwargs: dict[str, Any] = {"fetch_size": min(max(limit, 1), 1000)}
    database_name = clean_text(database)
    if database_name:
        session_kwargs["database"] = database_name

    with driver.session(**session_kwargs) as session:
        _ensure_group_id_index(session, database_name)
        _ensure_group_id_prop(session, database_name)
        _validate_query_type(
            session,
            query=query,
            parameters=safe_parameters,
            allow_write=allow_write,
        )
        result = session.run(query, safe_parameters)  # type: ignore[arg-type]
        return [record.data() for record in itertools.islice(result, limit)]


def run_read_queries(
    runtime: Any,
    queries: list[dict[str, Any]],
    *,
    database: str = "",
) -> list[list[dict[str, Any]]]:
    """在同一 Session 内批量执行多条只读查询，减少连接开销。"""
    uri, user, pwd = get_credentials(runtime)
    driver = get_driver(uri, user, pwd)

    database_name = clean_text(database)
    session_kwargs: dict[str, Any] = {"fetch_size": 1000}
    if database_name:
        session_kwargs["database"] = database_name

    results: list[list[dict[str, Any]]] = []
    with driver.session(**session_kwargs) as session:
        _ensure_group_id_index(session, database_name)
        _ensure_group_id_prop(session, database_name)
        for q in queries:
            query = q.get("query", "")
            parameters = q.get("parameters") or {}
            limit = int(q.get("limit", 100))
            result = session.run(query, parameters)  # type: ignore[arg-type]
            results.append([record.data() for record in itertools.islice(result, limit)])
    return results


def run_fulltext_query(
    runtime: Any,
    *,
    index_name: str,
    keyword: str,
    group_id: str = "",
    database: str = "",
    limit: int = 20,
) -> list[dict[str, Any]]:
    """使用全文索引查询节点。"""
    uri, user, pwd = get_credentials(runtime)
    driver = get_driver(uri, user, pwd)

    database_name = clean_text(database)
    session_kwargs: dict[str, Any] = {"fetch_size": min(max(limit, 1), 1000)}
    if database_name:
        session_kwargs["database"] = database_name

    group_filter = ""
    params: dict[str, Any] = {"keyword": keyword, "limit": limit}
    if group_id:
        group_filter = "WHERE node.group_id = $group_id"
        params["group_id"] = group_id

    query = (
        f"CALL db.index.fulltext.queryNodes($index_name, $keyword) "
        f"YIELD node, score "
        f"{group_filter} "
        f"RETURN node AS n, score "
        f"ORDER BY score DESC "
        f"LIMIT $limit"
    )
    params["index_name"] = index_name

    with driver.session(**session_kwargs) as session:
        _ensure_group_id_index(session, database_name)
        _ensure_group_id_prop(session, database_name)
        if not _ensure_fulltext_index(session, database_name):
            raise ValueError("全文索引不可用。")
        result = session.run(query, params)  # type: ignore[arg-type]
        return [record.data() for record in itertools.islice(result, limit)]


# ── 工具函数 ────────────────────────────────────────────────────────────────

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


def strip_embedding_fields(value: Any) -> Any:
    """递归移除响应中的 embedding 大字段，减少返回体积。"""
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            field_name = clean_text(key).lower()
            if field_name in {"embedding", "embedding_vector", "vector"}:
                continue
            result[str(key)] = strip_embedding_fields(item)
        return result
    if isinstance(value, list):
        return [strip_embedding_fields(item) for item in value]
    if isinstance(value, tuple):
        return [strip_embedding_fields(item) for item in value]
    if hasattr(value, "items"):
        try:
            return strip_embedding_fields(dict(value.items()))
        except Exception:
            return value
    return value


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


@dataclass(frozen=True)
class GraphRenderConfig:
    """图谱渲染尺寸配置。"""
    width_min: float
    width_factor: float
    width_max: float
    height_min: float
    height_factor: float
    height_max: float


_GROUP_GRAPH_RENDER_CONFIG = GraphRenderConfig(
    width_min=8.0, width_factor=0.35, width_max=20.0,
    height_min=6.0, height_factor=0.28, height_max=16.0,
)

_NODE_GRAPH_RENDER_CONFIG = GraphRenderConfig(
    width_min=7.0, width_factor=0.5, width_max=16.0,
    height_min=5.0, height_factor=0.35, height_max=12.0,
)


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
        config=_GROUP_GRAPH_RENDER_CONFIG,
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
        config=_NODE_GRAPH_RENDER_CONFIG,
    )


def _render_graph_png(
    *,
    nodes_map: dict[str, str],
    edges: list[tuple[str, str, str]],
    node_limit: int,
    center_node_id: str,
    empty_message: str,
    config: GraphRenderConfig,
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

    width = min(max(config.width_min, graph.number_of_nodes() * config.width_factor), config.width_max)
    height = min(max(config.height_min, graph.number_of_nodes() * config.height_factor), config.height_max)
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
