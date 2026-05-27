/** 力导向仿真参数：电荷力强度（负值表示排斥） */
export const FORCE_CHARGE = -800
/** 力导向仿真参数：连接边的理想距离 */
export const FORCE_LINK_DIST = 200
/** 力导向仿真参数：碰撞检测的节点间距 */
export const FORCE_COLLISION = 14
/** 力导向仿真参数：向中心点的回归拉力强度 */
export const FORCE_PULL = 0.01

/** 示例数据的节点名称池 */
export const DEMO_NAMES = ["Orchestrator", "Planner", "Memory", "Retriever", "Embedder", "Reasoner", "VectorDB", "Cache", "DocStore", "Gateway", "Auth", "Logger", "Executor", "Validator", "Critic", "Scheduler", "EventBus", "Metrics", "Router", "Parser", "Encoder", "Decoder", "Monitor", "Collector", "Dispatcher", "Registry", "Resolver", "Transformer", "Aggregator", "Connector", "Buffer", "Indexer", "Crawler", "Fetcher", "Loader", "Saver", "Tracker", "Analyzer", "Renderer", "Compiler", "Interpreter", "Assembler"]
/** 示例数据的节点类型池 */
export const DEMO_TYPES = ["System", "Component", "Database", "Model", "Task"]
/** 示例数据的关系类型池 */
export const DEMO_RELS = ["dispatches", "reads", "routes", "spawns", "queries", "invokes", "schedules", "indexes", "caches", "fetches", "embeds", "stores", "reviews", "checks", "verifies", "logs", "publishes", "submits", "drafts", "triggers", "reports", "tracks", "persists", "syncs", "writes", "refines", "retries", "mirrors", "calls", "depends"]
