/** 力导向仿真参数：电荷力强度（负值表示排斥） */
export const FORCE_CHARGE = -800
/** 力导向仿真参数：连接边的理想距离 */
export const FORCE_LINK_DIST = 200
/** 力导向仿真参数：碰撞检测的节点间距 */
export const FORCE_COLLISION = 14
/** 力导向仿真参数：向中心点的回归拉力强度 */
export const FORCE_PULL = 0.01

// ── 缩放与 LOD（细节层次）────────────────────────────────────────────

/** 最小缩放比例（万级节点适配全图后可能极小，故下限放低） */
export const ZOOM_MIN = 0.05
/** 最大缩放比例 */
export const ZOOM_MAX = 4

/**
 * LOD（细节层次）表：按缩放比例 k 划分「由远及近」的渲染策略。
 * 每一级的全部开关集中在此——新增或调整一级只改这个数组里的一项，不必同步多张平行数组。
 * 中景（mid）不按节点重要性筛选标签，而是用更大的 labelCell 自然稀疏化。
 *
 * - max：该级适用的最大缩放比例（含）；key 仅供调试识别
 * - stroke：节点描边宽度（0 = 不描边）
 * - minRadiusPx：节点最小屏幕半径（0 = 不钳制）
 * - edgeAlpha / edgeWidth：边线默认透明度与宽度
 * - arrow：箭头策略 none（不画）/ active（仅高亮边）/ all（全部）
 * - card：节点标签是否用白卡片承托
 * - labelCell：标签占位网格的格子边长，越大标签越稀疏
 * - nodeLabel：none（不显示）/ all（全部）
 * - edgeLabel：none / active（仅高亮边）/ all
 * - nodeLabelMax / edgeLabelMax：标签最大字数（9999 视为不截断）
 *
 * 注：标签策略为 none 时，悬停/选中的对象仍会强制显示标签——用户主动关注的内容必须可读。
 */
export const LOD_STYLE = [
  { key: 'far',   max: 0.35,     stroke: 0,   minRadiusPx: 1.2, edgeAlpha: 0.18, edgeWidth: 1.0, arrow: 'none',   card: false, labelCell: 22, nodeLabel: 'none', nodeLabelMax: 0,    edgeLabel: 'none',   edgeLabelMax: 0 },
  { key: 'mid',   max: 0.75,     stroke: 1.0, minRadiusPx: 1.5, edgeAlpha: 0.35, edgeWidth: 1.2, arrow: 'none',   card: false, labelCell: 26, nodeLabel: 'all',  nodeLabelMax: 6,    edgeLabel: 'none',   edgeLabelMax: 0 },
  { key: 'near',  max: 1.4,      stroke: 2.5, minRadiusPx: 0,   edgeAlpha: 0.55, edgeWidth: 1.6, arrow: 'active', card: false, labelCell: 16, nodeLabel: 'all',  nodeLabelMax: 8,    edgeLabel: 'active', edgeLabelMax: 14 },
  { key: 'close', max: 2.4,      stroke: 2.5, minRadiusPx: 0,   edgeAlpha: 0.55, edgeWidth: 1.6, arrow: 'all',    card: true,  labelCell: 14, nodeLabel: 'all',  nodeLabelMax: 16,   edgeLabel: 'all',    edgeLabelMax: 14 },
  { key: 'macro', max: Infinity, stroke: 3.0, minRadiusPx: 0,   edgeAlpha: 0.6,  edgeWidth: 1.8, arrow: 'all',    card: true,  labelCell: 12, nodeLabel: 'all',  nodeLabelMax: 9999, edgeLabel: 'all',    edgeLabelMax: 9999 },
]

/** 视口密度自适应：单位「千世界像素」内的可见节点数超过此值则 LOD 降一级 */
export const DENSITY_HIGH = 1.2
/** 视口密度低于此值则 LOD 升一级（上限 near） */
export const DENSITY_LOW = 0.25
/** LOD 等级切换迟滞比例，防止在阈值附近来回抖动 */
export const LOD_HYSTERESIS = 0.04
/** 单帧绘制的标签数量上限，保证极端密集时耗时有界 */
export const MAX_LABELS = 400

/** 节点标签屏幕字号：px = clamp(BASE + (k-1)*GROWTH, MIN, MAX) */
export const LABEL_BASE_PX = 11
export const LABEL_GROWTH = 1.6
export const LABEL_MIN_PX = 10
export const LABEL_MAX_PX = 20
/** 边标签屏幕字号基准与上限 */
export const EDGE_LABEL_BASE_PX = 9
export const EDGE_LABEL_MAX_PX = 16

// ── 相机（自动适配视图）──────────────────────────────────────────────

/** fitView 的世界坐标留白 */
export const FIT_PADDING = 72
/** fitView 允许的最大放大倍数，避免少量节点被放到糊 */
export const FIT_MAX_ZOOM = 1.25
/** fitView 动画时长（毫秒） */
export const FIT_DURATION = 500
/** 加载期间相机跟随的缓动时长（毫秒） */
export const CAMERA_FOLLOW_DURATION = 420
/** 首屏 fit 的延迟（毫秒），先让力导向把首批节点铺开一点再框 */
export const FIRST_FIT_DELAY = 80

// ── 加载入场动画 ─────────────────────────────────────────────────────

/** 单个节点的入场动画时长（毫秒） */
export const ENTER_DURATION = 260
/** 同批节点的入场错峰间隔（毫秒） */
export const ENTER_STAGGER = 12
/** 同批节点入场错峰的总上限（毫秒） */
export const ENTER_STAGGER_MAX = 240

// ── 悬停 ─────────────────────────────────────────────────────────────

/**
 * 悬停停留时长（毫秒）：鼠标需在同一节点/关系上停留这么久才触发悬浮聚焦。
 * 设为 0 表示即时触发（划过即高亮，节点密集时会满屏闪烁）。
 */
export const HOVER_DELAY_MS = 500
/** 新增数据时的仿真热度（alphaTarget） */
export const SIM_HEAT_NEW = 0.3
/** 新增数据热度保持时长（毫秒） */
export const SIM_HEAT_MS = 300

// ── 布局收敛 ─────────────────────────────────────────────────────────

/** 常态布局的冷却参数：alphaDecay 越大衰减越快，alpha 降到 alphaMin 即自动停止 */
export const LAYOUT_ALPHA_DECAY = 0.02
export const LAYOUT_ALPHA_MIN = 0.001
/**
 * 加载完成后的快速收敛参数。
 * d3 默认衰减率下，从 0.3 冷却到 0.001 需要约 300 帧（≈5 秒），加载完成后节点会继续漂移很久；
 * 用更陡的衰减与更高的收敛阈值，可把这段时间压到 ≈0.5 秒。
 */
export const LAYOUT_SETTLE_DECAY = 0.08
export const LAYOUT_SETTLE_MIN = 0.05

/** 离线快进布局的时间预算（毫秒）：加载完成后在一帧内推进布局，最多阻塞这么久 */
export const LAYOUT_SETTLE_BUDGET_MS = 320

// ── LOD 稳定性 ───────────────────────────────────────────────────────

/** LOD 等级的最短保持时长（毫秒）：节点移动会让密度逐帧抖动，限流后标签不再忽有忽无 */
export const LOD_CHANGE_MIN_MS = 200

// ── 大图降级 ─────────────────────────────────────────────────────────

/** 节点数超过此值时进入简化渲染模式（关闭阴影、光晕与卡片底卡） */
export const REDUCED_NODE_COUNT = 3000

/** 示例数据的节点名称池 */
export const DEMO_NAMES = ["Orchestrator", "Planner", "Memory", "Retriever", "Embedder", "Reasoner", "VectorDB", "Cache", "DocStore", "Gateway", "Auth", "Logger", "Executor", "Validator", "Critic", "Scheduler", "EventBus", "Metrics", "Router", "Parser", "Encoder", "Decoder", "Monitor", "Collector", "Dispatcher", "Registry", "Resolver", "Transformer", "Aggregator", "Connector", "Buffer", "Indexer", "Crawler", "Fetcher", "Loader", "Saver", "Tracker", "Analyzer", "Renderer", "Compiler", "Interpreter", "Assembler"]
/** 示例数据的节点类型池 */
export const DEMO_TYPES = ["System", "Component", "Database", "Model", "Task"]
/** 示例数据的关系类型池 */
export const DEMO_RELS = ["dispatches", "reads", "routes", "spawns", "queries", "invokes", "schedules", "indexes", "caches", "fetches", "embeds", "stores", "reviews", "checks", "verifies", "logs", "publishes", "submits", "drafts", "triggers", "reports", "tracks", "persists", "syncs", "writes", "refines", "retries", "mirrors", "calls", "depends"]
