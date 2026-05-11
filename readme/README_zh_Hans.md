# Neo4j Excel 图谱导入器

通过一次 Dify 工具调用，将结构化的 Excel 知识图谱文件写入 Neo4j。

## 支持的 Excel 格式

插件自动识别单个 Sheet 中交替出现的**节点表**和**关系表**：

| 表类型 | 必填列 |
|---|---|
| 节点表 | `NodeID` · `name` · `node_type` · `definition` · `level` · `grade_range` · `keywords` · `teaching_tip` |
| 关系表 | `SourceID` · `RelationType` · `TargetID` · *（可选）* `说明` |

章节标题行和空行会被自动跳过。

## 凭证配置

| 字段 | 说明 |
|---|---|
| `neo4j_uri` | Bolt 连接串，如 `bolt://localhost:7687` 或 `neo4j+s://xxxxx.databases.neo4j.io` |
| `neo4j_user` | 数据库用户名（默认 `neo4j`） |
| `neo4j_password` | 数据库密码 |

## 参数说明

| 参数 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `excel_url` | string | — | `.xlsx` 文件的公开 HTTPS URL |
| `excel_file` | file | — | 直接上传 `.xlsx` 文件（与 URL 二选一） |
| `batch_size` | number | `500` | 每次 Neo4j 事务写入的行数 |
| `clear_before_import` | boolean | `false` | 导入前删除数据库中所有节点和关系（⚠️ 危险） |

## 工作流输出变量

| 变量 | 类型 | 说明 |
|---|---|---|
| `nodes_count` | integer | 已写入节点数 |
| `rels_count` | integer | 已写入关系数 |
| `skipped_rels` | integer | 跳过的关系数（端点节点不存在） |
| `node_type_stats` | object | `{节点类型: 数量}` 映射 |
| `rel_type_stats` | object | `{关系类型: 数量}` 映射 |
| `summary` | string | 人类可读的导入报告 |

## 注意事项

- 全程使用 `MERGE`，重复执行不会产生重复数据（幂等）。
- 已安装 APOC 插件时，关系使用真实类型名（如 `[:包含方法]`）；否则退化为 `[:RELATED {relType: "…"}]`。
- 推荐内存配置：≥ 256 MB（含 pandas / openpyxl / neo4j driver）。
