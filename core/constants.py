from __future__ import annotations

from typing import Literal

NODE_LABEL = "KnowledgeNode"
DEFAULT_NODE_LABEL = "Node"
DEFAULT_REL_TYPE = "RELATED"
DEFAULT_DIRECTION: Literal["forward", "bidirectional"] = "forward"
DEFAULT_BATCH_SIZE = 500
DEFAULT_LIMIT = 20
MAX_LIMIT_STANDARD = 100
MAX_LIMIT_GROUP_GRAPH = 3000
VECTOR_INDEX_NAME = "knowledge_node_embedding_idx"

Direction = Literal["forward", "bidirectional"]

NODE_RESERVED_PROP_KEYS = frozenset({
    "uid", "name", "description", "group_id", "labels", "properties", "meta", "embedding",
})

RELATION_RESERVED_PROP_KEYS = frozenset({
    "source_uid", "target_uid", "rel_type", "group_id", "direction",
    "description", "weight", "properties", "meta",
})
