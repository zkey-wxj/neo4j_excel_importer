# Neo4j Graph Import, Query & Editor

**Dify 知识图谱全生命周期管理插件** — 一条连接串，打通 Excel/Markdown 到 Neo4j 图数据库的导入、查询、编辑与可视化全链路。

---

## 一、定位

面向使用 Dify 构建 AI 应用的团队，提供 **开箱即用的 Neo4j 图数据工具集**。LLM Agent 可通过 15 个工具直接操作知识图谱；业务用户可通过 Endpoint 页面在浏览器中完成图谱浏览与数据维护，无需编写 Cypher。

---

## 二、核心能力

### 1. 数据导入

| 工具 | 说明 |
|------|------|
| **import_graph** | 从 Excel URL / Markdown 表格 / 上传文件导入节点与关系，支持自定义字段映射、按 `group_id` 隔离数据集 |
| **extract_graph_data** | 将 Markdown 表格解析为结构化 JSON，可选导出 Excel/JSON，不写入数据库 |
| **neo4j_query** | 通用 Cypher 查询执行器，支持参数化查询与只读/写入模式切换 |

导入流程自动处理：

- 节点 `nid` 唯一约束与全文索引创建
- APOC 与原生 MERGE 双路径兼容
- 可选接入 Dify text-embedding 模型自动生成节点向量

### 2. 图谱查询（6 个工具）

| 工具 | 场景 |
|------|------|
| **query_node_by_id** | 按精确 `nid` 查询节点本体 + 关系 + 邻居，可选渲染子图 PNG |
| **query_nodes_by_nids** | 批量按 `nid` 列表查询，返回匹配节点与未命中列表 |
| **query_nodes_fuzzy** | 关键词模糊搜索（支持全文索引 → 向量检索 → CONTAINS 三级回退） |
| **query_relations_by_node** | 查询指定节点的全部出入关系，支持关系类型过滤 |
| **query_relations_between_nodes** | 查询两节点间有向关系 |
| **query_group_graph** | 分页查询整个 `group_id` 下的节点与关系，可选渲染全组图谱 PNG |

所有查询工具支持：

- `group_id` 隔离过滤
- 向量字段自动脱敏（不返回 embedding）
- 分页与截断标记

### 3. 数据写入（4 个工具）

| 工具 | 说明 |
|------|------|
| **save_node_single** | 保存单个节点（MERGE 语义） |
| **save_nodes_batch** | 批量保存节点，支持 embedding 自动生成 |
| **save_relation_single** | 保存单条关系 |
| **save_relations_batch** | 批量保存关系 |

### 4. 图谱运维（2 个工具）

| 工具 | 说明 |
|------|------|
| **get_group_labels** | 获取指定 `group_id` 下所有去重的节点 Labels |
| **copy_group_data** | 跨分组复制数据，自动处理 `nid` 冲突（同名合并 / 异名重命名），关系自动重写 |

---

## 三、Endpoint API & 可视化页面

插件提供 **15 个 RESTful API**，配合一个内嵌的 **D3.js 图谱可视化页面**，浏览器直接访问即可使用。

### API 一览

| 方法 | 路径 | 功能 |
|------|------|------|
| GET | `/group-graph` | 渲染图谱可视化 HTML 页面 |
| GET | `/group-graph/api/graph` | 分页查询图谱数据 |
| POST | `/group-graph/api/node` | 新增节点 |
| PUT | `/group-graph/api/node` | 更新节点 |
| DELETE | `/group-graph/api/node` | 删除节点 |
| POST | `/group-graph/api/relation` | 新增关系 |
| PUT | `/group-graph/api/relation` | 更新关系 |
| DELETE | `/group-graph/api/relation` | 删除关系 |
| POST | `/group-graph/api/replace-node-relations` | 节点关系整体迁移（合并节点） |
| GET | `/group-graph/api/neighbors` | N 跳邻居展开 |
| GET | `/group-graph/api/path` | 最短路径查找 |
| GET | `/group-graph/api/stats` | 图谱统计摘要 |
| DELETE | `/group-graph/api/group` | 清空分组数据 |
| GET | `/group-graph/api/export` | 导出 Excel/JSON |
| POST | `/group-graph/api/import` | 导入 Excel/JSON（支持 merge/override 模式） |

### 可视化页面功能

- **图谱渲染**：D3.js Force-Directed 布局，节点按 Label 着色，关系类型标注
- **节点详情**：点击节点查看属性、标签、描述，支持就地编辑
- **关系查看**：高亮相邻关系，展示关系类型与属性
- **搜索过滤**：按节点名称 / `nid` / Label 实时筛选
- **数据维护**：页面内直接新增/编辑/删除节点和关系
- **邻居展开**：点击节点展开 N 跳邻居子图
- **路径查找**：输入起点终点，高亮最短路径
- **导入导出**：上传 Excel/JSON 导入，导出当前分组数据

---

## 四、架构特点

```
┌─────────────────────────────────────────────────┐
│                  Dify Workflow                    │
│  ┌──────────┐  ┌──────────┐  ┌──────────────┐   │
│  │ LLM Agent│──│ 15 Tools │──│  Neo4j DB    │   │
│  └──────────┘  └──────────┘  └──────────────┘   │
│                      │                           │
│              ┌───────┴───────┐                   │
│              │  Endpoint API │                   │
│              │  + HTML 页面  │                   │
│              └───────────────┘                   │
└─────────────────────────────────────────────────┘
```

- **数据隔离**：所有数据通过 `group_id` 隔离，同一 Neo4j 实例可服务多个业务场景
- **Driver 复用**：按 `(uri, user)` 缓存连接，减少握手开销
- **向量可选**：接入 Dify embedding 模型后，节点自动具备向量检索能力
- **APOC 自适应**：运行时检测 APOC 可用性，无 APOC 环境自动降级为原生 Cypher
- **全量索引**：自动创建 `nid` 唯一约束、`group_id` 属性索引、全文检索索引

---

## 五、典型使用场景

1. **知识图谱构建**：Excel 录入知识点 → 导入 Neo4j → Agent 可查询
2. **RAG 增强**：向量化节点 → 模糊检索 → 结合 LLM 生成回答
3. **图谱维护**：业务人员通过 Endpoint 页面浏览、编辑、合并节点
4. **数据迁移**：`copy_group_data` 跨环境复制图谱，自动处理冲突
5. **图分析**：路径查找、邻居展开、统计摘要，支撑关系推理

---

## 六、凭据配置

| 字段 | 说明 |
|------|------|
| `neo4j_uri` | Bolt 连接串，如 `bolt://localhost:7687` 或 `neo4j+s://xxxxx.databases.neo4j.io` |
| `neo4j_user` | 数据库用户名（默认 `neo4j`） |
| `neo4j_password` | 数据库密码 |

---

## 七、支持的 Excel 格式

插件自动检测单 Sheet 中交替出现的 **节点表** 和 **关系表**：

| 表类型 | 必需列 |
|--------|--------|
| 节点表 | `NodeID` · `name` · `node_type`（可自定义映射） |
| 关系表 | `SourceID` · `RelationType` · `TargetID` |

章节标题行与空行自动跳过，额外列自动收入节点/关系的 `properties`。

---

## 八、注意事项

- 所有写入使用 `MERGE` 语义，幂等安全，可重复执行
- 若安装 APOC，关系以实际类型名创建（如 `[:包含方法]`）；否则使用通用 `[:RELATED]` 边 + `rel_type` 属性
- `group_id` 贯穿全部工具与 API，是数据隔离的核心维度
- 向量功能为可选，不配置 embedding 模型时自动走全文/文本回退
