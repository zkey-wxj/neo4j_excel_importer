import { useEffect, useRef, useCallback } from 'react'
import * as d3 from 'd3'
import { useAppStore } from '@/store'
import {
  FORCE_CHARGE,
  FORCE_LINK_DIST,
  FORCE_COLLISION,
  FORCE_PULL,
  ZOOM_MIN,
  ZOOM_MAX,
  LOD_STYLE,
  DENSITY_HIGH,
  DENSITY_LOW,
  LOD_HYSTERESIS,
  MAX_LABELS,
  LABEL_BASE_PX,
  LABEL_GROWTH,
  LABEL_MIN_PX,
  LABEL_MAX_PX,
  EDGE_LABEL_BASE_PX,
  EDGE_LABEL_MAX_PX,
  FIT_PADDING,
  FIT_MAX_ZOOM,
  FIT_DURATION,
  CAMERA_FOLLOW_DURATION,
  FIRST_FIT_DELAY,
  ENTER_DURATION,
  ENTER_STAGGER,
  ENTER_STAGGER_MAX,
  SIM_HEAT_NEW,
  SIM_HEAT_MS,
  LAYOUT_ALPHA_DECAY,
  LAYOUT_ALPHA_MIN,
  LAYOUT_SETTLE_DECAY,
  LAYOUT_SETTLE_MIN,
  LAYOUT_SETTLE_BUDGET_MS,
  LOD_CHANGE_MIN_MS,
  HOVER_DELAY_MS,
  REDUCED_NODE_COUNT,
} from '@/lib/constants'

// ── 常量定义 ────────────────────────────────────────────────────────
const MINIMAP_W = 180       // 小地图宽度（像素）
const MINIMAP_H = 130       // 小地图高度（像素）
const HIT_RADIUS = 18       // 节点点击检测半径（世界坐标）
const EDGE_HIT_TH = 8       // 关系边点击检测阈值（像素）
const DRAG_THRESHOLD = 4    // 拖拽判定阈值（像素），小于此值视为点击

// ── 辅助函数 ───────────────────────────────────────────────────────
/** 安全的字符串 trim */
function clean(v) {
  return String(v ?? '').trim()
}

/** 按最大字数截断文本，超出时追加省略号（max >= 9999 视为不截断） */
function clipText(text, max) {
  if (max >= 9999 || text.length <= max) return text
  return text.slice(0, max) + '…'
}

/**
 * 计算标签的屏幕字号（px）。
 * 字号随缩放温和增长，让「放大」有可见反馈；上下界防止过小看不清/过大喧宾夺主。
 */
function labelScreenPx(k, base, min, max) {
  return Math.min(max, Math.max(min, base + (k - 1) * LABEL_GROWTH))
}

/** 文本宽度缓存：`字体|文本` -> 宽度，避免每帧重复调用 measureText */
const textWCache = new Map()

/**
 * 带缓存的文本宽度测量。
 * 注意：调用前必须自行设置 c.font，命中缓存时本函数不会再设置字体。
 */
function measureTextCached(c, text, font) {
  const key = `${font}|${text}`
  let w = textWCache.get(key)
  if (w === undefined) {
    if (textWCache.size > 5000) textWCache.clear()
    w = c.measureText(text).width
    textWCache.set(key, w)
  }
  return w
}

/**
 * 由缩放比例与视口密度解析当前 LOD 等级索引。
 * @param {number} k 当前缩放比例
 * @param {number} density 视口密度（每千世界像素内的可见节点数）
 * @param {number} prev 上一帧的等级索引，用于迟滞、避免阈值附近闪烁
 */
function resolveLodIndex(k, density, prev) {
  let idx = 0
  while (idx < LOD_STYLE.length - 1 && k > LOD_STYLE[idx].max) idx++
  // 密度修正：稀疏图更早显示细节，密集图更早退化为点云（只允许 ±1 级）
  if (density > DENSITY_HIGH) idx = Math.max(0, idx - 1)
  else if (density > 0 && density < DENSITY_LOW) idx = Math.min(2, idx + 1)
  // 迟滞：等级回退时要求明显越过阈值，避免在边界来回抖动
  if (prev > idx) {
    const boundary = LOD_STYLE[prev - 1].max
    if (k > boundary * (1 - LOD_HYSTERESIS)) return prev
  }
  return idx
}

/**
 * 创建标签占位网格（每帧重建）。
 * 以格子为单位记录已被标签占用的区域，命中冲突的标签直接跳过，
 * 从而在密集区域自动取舍标签而不是让文本互相碾压。
 */
function createLabelGrid(cell) {
  const occupied = new Set()
  return {
    /**
     * 尝试占据矩形 [x0,y0]-[x1,y1]（世界坐标）。
     * @returns {boolean} 是否成功占据；false 表示与已有标签冲突
     */
    claim(x0, y0, x1, y1) {
      const gx0 = Math.floor(x0 / cell)
      const gx1 = Math.floor(x1 / cell)
      const gy0 = Math.floor(y0 / cell)
      const gy1 = Math.floor(y1 / cell)
      for (let gx = gx0; gx <= gx1; gx++) {
        for (let gy = gy0; gy <= gy1; gy++) {
          if (occupied.has(`${gx},${gy}`)) return false
        }
      }
      for (let gx = gx0; gx <= gx1; gx++) {
        for (let gy = gy0; gy <= gy1; gy++) occupied.add(`${gx},${gy}`)
      }
      return true
    },
  }
}

// ═══════════════════════════════════════════════════════════════════════
// useGraphCanvas
// 封装了所有的 Canvas 渲染、D3 力导向仿真和指针交互逻辑
// 替代了原始 group_graph.html 中的 GraphCanvas + GraphInteraction 类
// 核心职责：仿真驱动、画布绘制、缩放平移、节点拖拽、命中检测
//
// 参数说明（两者的分工不要合并）：
// - canvasRef：供事件回调在运行时读取当前元素（`canvasRef.current`），
//   避免闭包捕获到旧元素；
// - canvasEl：元素本身的状态，供事件绑定的 effect 作为依赖——
//   鉴权门会让 canvas 晚于 hook 挂载，只依赖稳定的 useCallback 会导致监听器永不绑定。
// ═══════════════════════════════════════════════════════════════════════
export default function useGraphCanvas(canvasRef, canvasEl) {
  // ── Zustand Store 响应式状态 ─────────────────────────────────────────
  const storeNodes = useAppStore((s) => s.nodes)
  const storeLinks = useAppStore((s) => s.links)
  const selectedNodeId = useAppStore((s) => s.selectedNodeId)
  const hoveredNodeId = useAppStore((s) => s.hoveredNodeId)
  const searchKeyword = useAppStore((s) => s.searchKeyword)
  const orphanFilter = useAppStore((s) => s.orphanFilter)
  const selectedLegendTypes = useAppStore((s) => s.selectedLegendTypes)
  const selectedRelTypes = useAppStore((s) => s.selectedRelTypes)
  const pathHighlight = useAppStore((s) => s.pathHighlight)
  const pickTarget = useAppStore((s) => s.pickTarget)
  const showMinimap = useAppStore((s) => s.showMinimap)
  const hoverFocus = useAppStore((s) => s.hoverFocus)
  const layoutPinned = useAppStore((s) => s.layoutPinned)
  const isLoadingData = useAppStore((s) => s.isLoadingData)
  const edgeCreating = useAppStore((s) => s.edgeCreating)
  const edgeSourceId = useAppStore((s) => s.edgeSourceId)
  const edgeMouseWorld = useAppStore((s) => s.edgeMouseWorld)
  const edgeTargetId = useAppStore((s) => s.edgeTargetId)

  const setSelectedNode = useAppStore((s) => s.setSelectedNode)
  const setHoveredNode = useAppStore((s) => s.setHoveredNode)
  const setZoom = useAppStore((s) => s.setZoom)
  const setDetailNode = useAppStore((s) => s.setDetailNode)
  const setPickTarget = useAppStore((s) => s.setPickTarget)
  const setPickedNid = useAppStore((s) => s.setPickedNid)
  const setStatus = useAppStore((s) => s.setStatus)
  const setEdgeCreating = useAppStore((s) => s.setEdgeCreating)
  const setEdgeSourceId = useAppStore((s) => s.setEdgeSourceId)
  const setEdgeMouseWorld = useAppStore((s) => s.setEdgeMouseWorld)
  const setEdgeTargetId = useAppStore((s) => s.setEdgeTargetId)
  const setEdgePopoverPos = useAppStore((s) => s.setEdgePopoverPos)

  // ── Refs（可变内部状态，不触发重渲染）────────────────────────────────
  const ctxRef = useRef(null)      // Canvas 2D 渲染上下文
  const W = useRef(0)              // 画布宽度（CSS 像素）
  const H = useRef(0)              // 画布高度（CSS 像素）
  const simRef = useRef(null)      // D3 力导向仿真实例
  const nodesRef = useRef([])      // 仿真节点数组（含 x/y/vx/vy 坐标）
  const edgesRef = useRef([])      // 处理后的关系数组（含曲率信息）
  const graphNodesRef = useRef([]) // 原始节点数据（用于过滤判断）
  const graphLinksRef = useRef([]) // 原始关系数据（用于过滤判断）
  const transformRef = useRef(d3.zoomIdentity)  // 当前画布变换矩阵
  const quadtreeRef = useRef(null) // 四叉树（用于高效的节点命中检测）
  const adjacencyRef = useRef(new Map()) // 邻接表：id -> Set<邻居 id>，O(1) 邻居判定
  const sortedNodesRef = useRef([]) // 按权重升序排列的节点快照，避免每帧重排
  const nodeMapRef = useRef(new Map()) // id -> 原始节点，命中检测 O(1) 查找
  const needRender = useRef(false) // 是否有待渲染的帧
  const rafId = useRef(null)       // requestAnimationFrame ID


  // 相机与动画
  const cameraAnimRef = useRef(null)      // 相机缓动动画的 rAF ID
  const userOverrodeCameraRef = useRef(false) // 用户是否手动接管了视图
  const lodStateRef = useRef({ idx: -1, at: 0 }) // 当前 LOD 等级与上次变更时间（迟滞 + 节流）
  const settlingRef = useRef(false)       // 是否正在离线快进布局（快进期间跳过 tick 回调开销）
  const animatingRef = useRef(false)      // 本帧是否有入场动画未播完
  const hoverTimerRef = useRef(null)      // 悬停停留（dwell）定时器
  const hoverPendingRef = useRef(null)    // 正在等待停留满足的悬停目标键（节点/边）
  const heatTimerRef = useRef(null)       // 仿真热度保持的定时器
  const layoutPinnedRef = useRef(false)   // 布局是否已被用户固定（固定后新数据不再唤醒仿真）
  const firstFitDoneRef = useRef(false)   // 本次加载是否已完成首屏适配
  const firstFitTimerRef = useRef(null)   // 首屏适配的延迟定时器
  const prevLoadingRef = useRef(false)    // 上一轮的加载状态，用于识别加载起止沿
  const hoveredRawRef = useRef(null)      // 原始悬停节点 id（不受「选中后仅关注邻居」过滤影响）

  // 交互状态
  const hoveredEdgeIdx = useRef(-1)  // 当前悬停的关系边索引
  const dragging = useRef(null)      // 正在拖拽的节点对象
  const dragWX = useRef(0)           // 拖拽起始世界坐标 X
  const dragWY = useRef(0)           // 拖拽起始世界坐标 Y
  const isDrag = useRef(false)       // 是否已进入拖拽状态（超过阈值）
  const panning = useRef(false)      // 是否正在平移画布
  const panX = useRef(0)             // 平移起始屏幕坐标 X
  const panY = useRef(0)             // 平移起始屏幕坐标 Y
  const panTX = useRef(0)            // 平移起始变换偏移 X
  const panTY = useRef(0)            // 平移起始变换偏移 Y
  const downX = useRef(0)            // 鼠标按下时的屏幕坐标 X
  const downY = useRef(0)            // 鼠标按下时的屏幕坐标 Y

  // 缓存 store 快照到 ref，使绘制代码可读取最新状态而不触发每次按键重渲染
  const storeSnap = useRef({
    selectedNodeId: null,
    hoveredNodeId: null,
    hoverFocus: true,
    searchKeyword: '',
    orphanFilter: false,
    selectedLegendTypes: new Set(),
    selectedRelTypes: new Set(),
    pathHighlight: null,
    pickTarget: null,
    edgeCreating: false,
    edgeSourceId: null,
    edgeMouseWorld: null,
    edgeTargetId: null,
  })

  // 保持快照与 store 同步
  useEffect(() => {
    storeSnap.current = {
      selectedNodeId,
      hoveredNodeId,
      hoverFocus,
      searchKeyword,
      orphanFilter,
      selectedLegendTypes,
      selectedRelTypes,
      pathHighlight,
      pickTarget,
      edgeCreating,
      edgeSourceId,
      edgeMouseWorld,
      edgeTargetId,
    }
  }, [
    selectedNodeId,
    hoveredNodeId,
    hoverFocus,
    searchKeyword,
    orphanFilter,
    selectedLegendTypes,
    selectedRelTypes,
    pathHighlight,
    pickTarget,
    edgeCreating,
    edgeSourceId,
    edgeMouseWorld,
    edgeTargetId,
  ])

  // ── Canvas 初始化 ────────────────────────────────────────────────────
  /** 初始化画布尺寸和 DPR 缩放，设置渲染上下文 */
  const initCanvas = useCallback(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const w = window.innerWidth
    const h = window.innerHeight
    W.current = w
    H.current = h
    const dpr = window.devicePixelRatio || 1
    canvas.width = w * dpr
    canvas.height = h * dpr
    canvas.style.width = w + 'px'
    canvas.style.height = h + 'px'
    const ctx = canvas.getContext('2d')
    ctx.scale(dpr, dpr)
    ctxRef.current = ctx
  }, [canvasRef])

  // ── 四叉树构建 ───────────────────────────────────────────────────────
  /** 构建四叉树索引，用于高效的空间近邻查询（节点命中检测） */
  const buildQuadtree = useCallback(() => {
    quadtreeRef.current = d3
      .quadtree()
      .x((d) => d.x ?? 0)
      .y((d) => d.y ?? 0)
      .addAll(nodesRef.current)
  }, [])

  // ── 坐标转换辅助 ─────────────────────────────────────────────────────
  /** 将屏幕坐标转换为世界坐标（考虑当前缩放和平移变换） */
  const toWorld = useCallback((ex, ey) => {
    const canvas = canvasRef.current
    if (!canvas) return [0, 0]
    const r = canvas.getBoundingClientRect()
    return transformRef.current.invert([ex - r.left, ey - r.top])
  }, [canvasRef])

  // ── 过滤辅助函数 ─────────────────────────────────────────────────────
  /** 获取节点的标签 key 列表（小写），用于图例类型匹配 */
  const getNodeLabelKeys = useCallback((n) => {
    const labels = Array.isArray(n?.raw?.labels) ? n.raw.labels : []
    const keys = labels.map((x) => clean(x).toLowerCase()).filter(Boolean)
    return keys.length ? keys : [clean(n?.type || 'Node').toLowerCase()]
  }, [])

  /**
   * 节点过滤判断：检查节点是否通过当前所有筛选条件
   * 包括：图例类型筛选、孤立节点筛选、关键词搜索
   */
  const passFilter = useCallback((n) => {
    const snap = storeSnap.current
    if (snap.selectedLegendTypes.size) {
      const keys = getNodeLabelKeys(n)
      if (!keys.some((k) => snap.selectedLegendTypes.has(k))) return false
    }
    if (snap.orphanFilter && (n.degree || 0) > 0) return false
    if (!snap.searchKeyword) return true
    const ll = Array.isArray(n?.raw?.labels) ? n.raw.labels : []
    return [
      clean(n?.label).toLowerCase(),
      clean(n?.nid).toLowerCase(),
      clean(n?.id).toLowerCase(),
      clean(n?.type).toLowerCase(),
      ...ll.map((x) => clean(x).toLowerCase()),
    ]
      .join('|')
      .includes(snap.searchKeyword)
  }, [getNodeLabelKeys])

  /** 关系边过滤判断：检查关系类型是否在选中的图例类型集合中 */
  const passEdgeFilter = useCallback((edge) => {
    const snap = storeSnap.current
    if (!snap.selectedRelTypes.size) return true
    return snap.selectedRelTypes.has(clean(edge.name).toLowerCase())
  }, [])

  // ── 贝塞尔曲线辅助函数 ─────────────────────────────────────────────────
  /** 计算贝塞尔曲线的控制点坐标（用于弯曲关系边的绘制） */
  const bezCp = useCallback((e) => {
    const s = e.source
    const t = e.target
    const sx = s.x ?? 0
    const sy = s.y ?? 0
    const tx = t.x ?? 0
    const ty = t.y ?? 0
    if (!e.curvature) return { cx: (sx + tx) / 2, cy: (sy + ty) / 2 }
    const dx = tx - sx
    const dy = ty - sy
    const dist = Math.sqrt(dx * dx + dy * dy) || 1
    const off = Math.max(35, dist * (0.25 + (e.pairTotal || 1) * 0.05))
    return {
      cx: (sx + tx) / 2 - (dy / dist) * e.curvature * off,
      cy: (sy + ty) / 2 + (dx / dist) * e.curvature * off,
    }
  }, [])

  /** 计算贝塞尔曲线的中点坐标（用于放置关系标签） */
  const bezMid = useCallback((e) => {
    const s = e.source
    const t = e.target
    const sx = s.x ?? 0
    const sy = s.y ?? 0
    const tx = t.x ?? 0
    const ty = t.y ?? 0
    if (e.isSelfLoop) return { x: sx + 70, y: sy }
    const c = bezCp(e)
    return { x: 0.25 * sx + 0.5 * c.cx + 0.25 * tx, y: 0.25 * sy + 0.5 * c.cy + 0.25 * ty }
  }, [bezCp])

  /** 计算贝塞尔曲线上参数 u 处的点坐标（用于边的命中检测） */
  const bezPt = useCallback((e, u) => {
    const s = e.source
    const t = e.target
    const sx = s.x ?? 0
    const sy = s.y ?? 0
    const tx = t.x ?? 0
    const ty = t.y ?? 0
    if (e.isSelfLoop) {
      const a = u * Math.PI * 2
      return { x: sx + 30 * Math.cos(a), y: sy + 30 * Math.sin(a) - 15 }
    }
    const c = bezCp(e)
    const w = 1 - u
    return {
      x: w * w * sx + 2 * w * u * c.cx + u * u * tx,
      y: w * w * sy + 2 * w * u * c.cy + u * u * ty,
    }
  }, [bezCp])

  // ── 命中检测 ────────────────────────────────────────────────────────
  /** 在世界坐标 (wx, wy) 处查找节点，使用四叉树加速 */
  const findNodeAt = useCallback(
    (wx, wy) => {
      const nd = quadtreeRef.current?.find(wx, wy, HIT_RADIUS) ?? null
      if (!nd) return null
      const snap = storeSnap.current
      if (snap.pathHighlight && !snap.pathHighlight.has(nd.id)) return null
      const g = nodeMapRef.current.get(nd.id)
      if (g && !passFilter(g)) return null
      return nd
    },
    [passFilter],
  )

  /** 在世界坐标 (wx, wy) 处查找关系边，沿贝塞尔曲线采样检测 */
  const findEdgeAt = useCallback(
    (wx, wy) => {
      const edges = edgesRef.current
      const snap = storeSnap.current
      for (let i = edges.length - 1; i >= 0; i--) {
        const e = edges[i]
        const s = e.source
        const t = e.target
        if (!s?.x || !t?.x || !passEdgeFilter(e)) continue
        if (snap.pathHighlight) {
          const ek = `${s.id}=>${t.id}`
          const ekR = `${t.id}=>${s.id}`
          if (!snap.pathHighlight.has(ek) && !snap.pathHighlight.has(ekR)) continue
        }
        const sg = nodeMapRef.current.get(s.id)
        const tg = nodeMapRef.current.get(t.id)
        if (!sg || !tg || !passFilter(sg) || !passFilter(tg)) continue
        // 直线边用包围盒预筛，剔除远离光标的边以减少贝塞尔采样开销
        if (
          !e.curvature &&
          (wx < Math.min(s.x, t.x) - EDGE_HIT_TH ||
            wx > Math.max(s.x, t.x) + EDGE_HIT_TH ||
            wy < Math.min(s.y, t.y) - EDGE_HIT_TH ||
            wy > Math.max(s.y, t.y) + EDGE_HIT_TH)
        )
          continue
        for (let u = 0; u <= 1; u += 0.05) {
          const p = bezPt(e, u)
          const dx = p.x - wx
          const dy = p.y - wy
          if (Math.sqrt(dx * dx + dy * dy) < EDGE_HIT_TH) return i
        }
      }
      return -1
    },
    [passEdgeFilter, passFilter, bezPt],
  )

  // ── 邻居判断 ─────────────────────────────────────────────────────
  /** 判断两个节点是否通过某条关系边直接相连 */
  const isNeighbor = useCallback((a, b) => {
    return adjacencyRef.current.get(a)?.has(b) || false
  }, [])

  // ── 绘制辅助函数 ───────────────────────────────────────────────────
  /**
   * 绘制所有关系边
   * 包括：视口裁剪、透明度计算、贝塞尔曲线/直线绘制、箭头绘制、标签绘制
   * @param {CanvasRenderingContext2D} c - Canvas 渲染上下文
   * @param {number} vx0/vy0/vx1/vy1 - 视口世界坐标范围
   * @param {number} k - 当前缩放比例
   * @param {string|null} focusN - 聚焦节点 ID
   * @param {object|null} feObj - 悬停的关系边对象
   * @param {Set|null} feIds - 悬停关系边的端点 ID 集合
   * @param {number} ae - 激活的关系边索引
   */
  const drawEdges = useCallback(
    (c, vx0, vy0, vx1, vy1, k, focusN, feObj, ae, vis, lod) => {
      const style = LOD_STYLE[lod]
      // 远景/中景只画直线：省掉每条边两次贝塞尔采样，是万级边可交互的前提
      const useCurve = lod >= 2
      const arrowMode = style.arrow
      const hasF = !!(focusN || feObj)
      const ph = storeSnap.current.pathHighlight
      // 悬浮聚焦开关：关闭时悬停不再压暗无关元素（节点密集时避免「一片灰色」）
      const dimming = storeSnap.current.hoverFocus

      // 分桶合批：key 为「描边色|线宽|透明度」，同桶的边共用一次 stroke。
      // 未高亮时全部边落入同一个桶，1 万条边只需要 1 次 stroke。
      const buckets = new Map()
      // 箭头按「颜色|透明度」分桶，收集扁平坐标 [ax, ay, angle, ...]
      const arrows = new Map()

      edgesRef.current.forEach((edge, i) => {
        const s = edge.source
        const t = edge.target
        if (!s || !t) return
        const sx = s.x
        const sy = s.y
        const tx = t.x
        const ty = t.y
        if (
          !Number.isFinite(sx) ||
          !Number.isFinite(sy) ||
          !Number.isFinite(tx) ||
          !Number.isFinite(ty)
        )
          return
        // 视口裁剪：跳过完全不在可视区域内的边
        if (
          Math.max(sx, tx) < vx0 ||
          Math.min(sx, tx) > vx1 ||
          Math.max(sy, ty) < vy0 ||
          Math.min(sy, ty) > vy1
        )
          return
        if (!vis.has(s.id) || !vis.has(t.id) || !passEdgeFilter(edge)) return

        const edgeKey = `${s.id}=>${t.id}`
        const edgeKeyR = `${t.id}=>${s.id}`
        const onPath = ph && (ph.has(edgeKey) || ph.has(edgeKeyR))
        const isE = i === ae
        const isNL = !!focusN && (s.id === focusN || t.id === focusN)
        const act = isE || isNL

        // 透明度：路径高亮 / 聚焦 / 悬停综合决定；无任何聚焦时用 LOD 默认透明度
        let alpha
        if (ph && hasF) {
          alpha = act ? 1 : onPath ? 0.2 : dimming ? 0.05 : 0.3
        } else if (ph) {
          alpha = onPath ? 1 : 0.08
        } else {
          alpha = hasF && dimming ? (act ? 1 : 0.1) : style.edgeAlpha
        }

        const color = onPath ? '#d63e52' : act ? '#3b5bdb' : '#C0C0C0'
        const width = (onPath || act ? 2.5 : style.edgeWidth) / k
        const key = `${color}|${width}|${alpha}`
        let bucket = buckets.get(key)
        if (!bucket) {
          bucket = { color, width, alpha, segs: [] }
          buckets.set(key, bucket)
        }
        bucket.segs.push(edge, sx, sy, tx, ty)

        // 箭头：none 不画 / active 只画高亮边 / all 全部画
        if (arrowMode !== 'none' && (arrowMode === 'all' || act) && !edge.isSelfLoop) {
          const tr = t.r ?? 10
          let ax
          let ay
          let angle
          if (useCurve && edge.curvature) {
            const cp2 = bezCp(edge)
            const u = 0.92
            const w1 = 1 - u
            ax = w1 * w1 * sx + 2 * w1 * u * cp2.cx + u * u * tx
            ay = w1 * w1 * sy + 2 * w1 * u * cp2.cy + u * u * ty
            const u2 = 0.93
            const w2 = 1 - u2
            const bx = w2 * w2 * sx + 2 * w2 * u2 * cp2.cx + u2 * u2 * tx
            const by = w2 * w2 * sy + 2 * w2 * u2 * cp2.cy + u2 * u2 * ty
            angle = Math.atan2(by - ay, bx - ax)
          } else {
            const dx = tx - sx
            const dy = ty - sy
            const dist = Math.sqrt(dx * dx + dy * dy) || 1
            ax = tx - (dx / dist) * tr
            ay = ty - (dy / dist) * tr
            angle = Math.atan2(dy, dx)
          }
          const akey = `${color}|${alpha}`
          let arr = arrows.get(akey)
          if (!arr) {
            arr = []
            arrows.set(akey, arr)
          }
          arr.push(ax, ay, angle)
        }
      })

      // 线宽小的先画，高亮/路径边自然压在上层
      const ordered = [...buckets.values()].sort((a, b) => a.width - b.width)
      ordered.forEach((b) => {
        c.globalAlpha = b.alpha
        c.strokeStyle = b.color
        c.lineWidth = b.width
        c.beginPath()
        const segs = b.segs
        for (let i = 0; i < segs.length; i += 5) {
          const e = segs[i]
          const ex = segs[i + 1]
          const ey = segs[i + 2]
          const fx = segs[i + 3]
          const fy = segs[i + 4]
          if (e.isSelfLoop) {
            c.moveTo(ex + 42, ey - 20)
            c.arc(ex + 20, ey - 20, 22, 0, Math.PI * 2)
          } else if (useCurve && e.curvature) {
            const cp = bezCp(e)
            c.moveTo(ex, ey)
            c.quadraticCurveTo(cp.cx, cp.cy, fx, fy)
          } else {
            c.moveTo(ex, ey)
            c.lineTo(fx, fy)
          }
        }
        c.stroke()
      })

      // 箭头合批：直接算三角形顶点，省掉逐箭头的 save/translate/rotate/restore
      const sz = Math.max(6, 8 / k)
      const half = sz * 0.5
      arrows.forEach((arr, akey) => {
        const sep = akey.lastIndexOf('|')
        c.globalAlpha = Number(akey.slice(sep + 1))
        c.fillStyle = akey.slice(0, sep)
        c.beginPath()
        for (let i = 0; i < arr.length; i += 3) {
          const ax = arr[i]
          const ay = arr[i + 1]
          const angle = arr[i + 2]
          const co = Math.cos(angle)
          const si = Math.sin(angle)
          c.moveTo(ax, ay)
          c.lineTo(ax - sz * co + half * si, ay - sz * si - half * co)
          c.lineTo(ax - sz * co - half * si, ay - sz * si + half * co)
          c.closePath()
        }
        c.fill()
      })

      c.globalAlpha = 1
    },
    [passEdgeFilter, bezCp],
  )

  /**
   * 绘制关系标签。
   * 独立于边线条单独成帧：这样节点标签可以先占据标签网格、拥有更高优先级。
   * LOD 规则：far/mid 不画，near 只画高亮边，close/macro 画全部。
   */
  const drawEdgeLabels = useCallback(
    (c, k, lod, focusN, feObj, ae, vis, grid) => {
      const style = LOD_STYLE[lod]
      const mode = style.edgeLabel
      // 低 LOD（far/mid）原本一条边标签都不画，但这会把用户悬停/选中的关系也藏掉；
      // 改为「有受关注对象时至少为高亮边显示标签」——高亮边数量极少，代价可忽略。
      const rawHover = hoveredRawRef.current
      if (
        mode === 'none' &&
        !focusN &&
        !feObj &&
        !rawHover &&
        !storeSnap.current.pathHighlight
      )
        return
      const maxChars = Math.max(style.edgeLabelMax, 14)
      const hasF = !!(focusN || feObj)
      const ph = storeSnap.current.pathHighlight
      const px = labelScreenPx(k, EDGE_LABEL_BASE_PX, EDGE_LABEL_BASE_PX, EDGE_LABEL_MAX_PX)
      const fs = px / k
      const font = `${fs}px ui-monospace, monospace`
      const pad = 4 / k
      const bh = fs * 1.4 + 4 / k
      let drawn = 0

      /** 绘制单条关系标签，占位失败（与已有标签冲突）时跳过 */
      const drawOne = (edge, act, force = false) => {
        if (drawn >= MAX_LABELS) return
        const s = edge.source
        const t = edge.target
        if (!s || !t || !vis.has(s.id) || !vis.has(t.id)) return
        if (!Number.isFinite(s.x) || !Number.isFinite(t.x)) return
        const name = edge.name
        if (!name) return
        const m = bezMid(edge)
        const lb = clipText(name, maxChars)
        c.font = font
        const tw = measureTextCached(c, lb, font)
        const x0 = m.x - tw / 2 - pad
        const y0 = m.y - bh / 2
        // force 模式（悬停/选中相关的边）：撞位也要画，保证关联关系的标签不缺失
        const claimed = !grid || grid.claim(x0, y0, x0 + tw + pad * 2, y0 + bh)
        if (!claimed && !force) return
        drawn++
        c.globalAlpha = 1
        c.fillStyle = act ? 'rgba(155,89,182,0.12)' : 'rgba(255,255,255,0.95)'
        c.beginPath()
        c.roundRect(x0, y0, tw + pad * 2, bh, 3 / k)
        c.fill()
        c.fillStyle = act ? '#3b5bdb' : '#666'
        c.textAlign = 'center'
        c.textBaseline = 'middle'
        c.fillText(lb, m.x, m.y)
      }

      // 高亮边优先占位，保证悬停/选中的关系标签一定可见
      const primary = []
      const rest = []
      edgesRef.current.forEach((edge, i) => {
        const s = edge.source
        const t = edge.target
        if (!s || !t) return
        const isE = i === ae
        const isNL = !!focusN && (s.id === focusN || t.id === focusN)
        const edgeKey = `${s.id}=>${t.id}`
        const edgeKeyR = `${t.id}=>${s.id}`
        const onPath = ph && (ph.has(edgeKey) || ph.has(edgeKeyR))
        const isNH =
          !!hoveredRawRef.current &&
          (s.id === hoveredRawRef.current || t.id === hoveredRawRef.current)
        const act = isE || isNL || onPath || isNH
        // 有聚焦时只保留相关标签，避免无关文本干扰
        if (hasF && !act) return
        if (mode !== 'all' && !act) return
        ;(act ? primary : rest).push([edge, act])
      })
      primary.forEach(([e, act]) => drawOne(e, act, true))
      if (mode === 'all') rest.forEach(([e, act]) => drawOne(e, act))
      c.globalAlpha = 1
    },
    [bezMid],
  )

  /**
   * 绘制所有节点
   * 包括：视口裁剪、路径/聚焦高亮发光、圆形填充和描边、名称标签
   * 节点按权重升序绘制，权重大的在上层
   */
  const drawNodes = useCallback(
    (c, vx0, vy0, vx1, vy1, k, focusN, feObj, feIds, sn, vis, lod, grid, now, reduced) => {
      const style = LOD_STYLE[lod]
      const labelMode = style.nodeLabel
      const labelMax = style.nodeLabelMax
      const hasF = !!(focusN || feObj)
      const ph = storeSnap.current.pathHighlight
      // 悬浮聚焦开关：关闭时悬停不再压暗无关节点（节点密集时避免「一片灰色」）
      const dimming = storeSnap.current.hoverFocus
      const adj = adjacencyRef.current
      // 字号随缩放温和增长：屏幕字号 = clamp(BASE + (k-1)*GROWTH, MIN, MAX)
      const fs = labelScreenPx(k, LABEL_BASE_PX, LABEL_MIN_PX, LABEL_MAX_PX) / k
      const fsStrong =
        labelScreenPx(k, LABEL_BASE_PX + 1, LABEL_MIN_PX, LABEL_MAX_PX + 2) / k
      const font = `500 ${fs}px 'JetBrains Mono',monospace`
      const fontStrong = `600 ${fsStrong}px 'JetBrains Mono',monospace`

      /**
       * 统一的透明度判定（节点本体与节点标签共用）。
       * 关注对象保持不透明 → 路径高亮项次之 → 其余压暗；
       * 未开启「悬浮聚焦」时不做压暗，避免节点密集时悬停后一片灰。
       */
      const resolveAlpha = (focusing, onPath) => {
        if (ph && hasF) return focusing ? 1 : onPath ? 0.2 : dimming ? 0.05 : 0.3
        if (ph) return onPath ? 1 : 0.1
        return hasF && dimming ? (focusing ? 1 : 0.15) : 1
      }

      // ── 第一趟：节点本体（光晕、圆、描边），按权重升序绘制使大节点压在上层
      sortedNodesRef.current.forEach((nd) => {
        const x = nd.x ?? 0
        const y = nd.y ?? 0
        if (x < vx0 || x > vx1 || y < vy0 || y > vy1) return
        if (!vis.has(nd.id)) return

        const onPath = ph && ph.has(nd.id)
        const isNF = nd.id === focusN
        const isNN = !!focusN && !isNF && (adj.get(focusN)?.has(nd.id) || false)
        const isEF = feIds?.has(nd.id)
        const fc = isNF || !!isEF || nd.id === sn

        // 入场动画：新节点半径从 45% 弹出、同步淡入（appearAt 为 0 表示已播完）
        let appear = 1
        if (nd.appearAt) {
          const ap = (now - nd.appearAt) / ENTER_DURATION
          if (ap < 1) {
            appear = ap > 0 ? ap : 0
            animatingRef.current = true
          } else {
            nd.appearAt = 0
          }
        }
        if (appear <= 0) return
        const ease = 1 - Math.pow(1 - appear, 3)

        const alpha = resolveAlpha(fc || isNN, onPath) * ease

        let r = (nd.r ?? 10) * (0.45 + 0.55 * ease)
        // 全景级别保证最小屏幕半径，否则节点会缩成看不见的尘点
        if (style.minRadiusPx > 0) r = Math.max(r, style.minRadiusPx / k)

        // 路径高亮：红色光环
        if (onPath && (!ph || !hasF || fc || isNN)) {
          c.beginPath()
          c.arc(x, y, r + 6 / k, 0, Math.PI * 2)
          c.strokeStyle = '#d63e52'
          c.lineWidth = 2.5 / k
          c.globalAlpha = Math.min(alpha, 0.5)
          c.stroke()
        }

        // 选中/悬停光晕：低 LOD 下这是唯一的视觉引导，故始终保留
        if (fc) {
          c.beginPath()
          c.arc(x, y, r + 5 / k, 0, Math.PI * 2)
          c.strokeStyle = nd.color
          c.lineWidth = 2.5 / k
          c.globalAlpha = 0.35 * ease
          c.stroke()
        }

        c.globalAlpha = alpha
        c.beginPath()
        c.arc(x, y, r, 0, Math.PI * 2)
        c.fillStyle = nd.color
        c.fill()
        // 点云模式不描边：大图下直接省掉一半的绘制调用
        if (style.stroke > 0 && !reduced) {
          c.strokeStyle = fc ? '#333' : '#fff'
          c.lineWidth = (fc ? 3 : style.stroke) / k
          c.stroke()
        }
        c.globalAlpha = 1
      })

      // ── 第二趟：节点标签
      // 按权重降序遍历（大节点优先占位），命中占位冲突的标签直接跳过，
      // 名字最长、权重最大的节点总能保住自己的标签。
      // ── 第二趟：节点标签 ──
      // 分两轮绘制：第一轮画「与聚焦/悬停/选中相关」的标签（无视当前 LOD 是否允许
      // 显示标签——用户主动关注的内容必须可读），第二轮画其余标签；
      // 其余标签按权重降序占位，位置冲突即跳过，保证大节点优先保住自己的标签。
      const arr = sortedNodesRef.current
      let drawn = 0
      // 有悬停/选中聚焦时不再绘制普通标签：此时用户只关心相关节点，
      // 画面更干净，也能把 MAX_LABELS 的额度全留给「关联节点的标签」
      const otherLabels = labelMode !== 'none' && !hasF

      // 核心关注节点：鼠标指到的节点、聚焦节点、选中节点、悬停关系的两端
      const rawHover = hoveredRawRef.current
      const isCoreNode = (nd) =>
        nd.id === rawHover ||
        nd.id === focusN ||
        nd.id === sn ||
        !!feIds?.has(nd.id)
      /** 相关节点：核心关注节点，或其直接邻居 */
      // 邻居以「聚焦节点」为基准；若当前只有原始悬停节点（选中态下悬停非邻居会被过滤掉），
      // 也把它的邻居纳入关联，保证「指到谁就能看到谁的周边」
      const relatedBase = focusN || rawHover
      const isRelatedNode = (nd) =>
        isCoreNode(nd) || (!!relatedBase && (adj.get(relatedBase)?.has(nd.id) || false))

      /** 尝试绘制单个节点标签（受占位网格与数量上限约束） */
      const drawLabel = (nd, force = false) => {
        if (drawn >= MAX_LABELS) return
        if (!vis.has(nd.id)) return
        const x = nd.x
        const y = nd.y
        if (!Number.isFinite(x) || !Number.isFinite(y)) return
        if (x < vx0 || x > vx1 || y < vy0 || y > vy1) return

        const name = nd.name || nd.label || ''
        if (!name) return

        const related = isRelatedNode(nd)
        const strong = related && !ph
        const lb = clipText(name, strong ? Math.max(labelMax, 16) : Math.max(labelMax, 8))
        const f = strong ? fontStrong : font
        const sz = strong ? fsStrong : fs
        c.font = f
        const tw = measureTextCached(c, lb, f)

        const r = nd.r ?? 10
        const pad = 5 / k
        const lx = x + r + 5 / k
        const cardH = sz + 8 / k
        const x0 = lx - pad
        const y0 = y - cardH / 2
        // 标签占位：与已有标签冲突时放弃绘制，避免文本互相碾压。
        // force 模式（悬停/选中相关的节点）即便撞位也要画出来——否则用户悬停一个节点时，
        // 总有邻居因为位置相近而「莫名少一个标签」，这正是用户要的「关联节点标签必须可见」。
        const claimed = !grid || grid.claim(x0, y0, x0 + tw + pad * 2, y0 + cardH)
        if (!claimed && !force) return
        drawn++

        const onPath = ph && ph.has(nd.id)
        const alpha = resolveAlpha(related, onPath)

        // 相关节点在低 LOD 下也用白卡片承托，保证缩小时依然清晰可读
        const useCard = style.card || lod < 2
        if (strong && useCard) {
          c.globalAlpha = alpha
          c.shadowColor = 'rgba(0,0,0,0.18)'
          c.shadowBlur = reduced ? 0 : 6 / k
          c.shadowOffsetY = reduced ? 0 : 2 / k
          c.fillStyle = '#fff'
          c.beginPath()
          c.roundRect(x0, y0, tw + pad * 2, cardH, cardH / 2)
          c.fill()
          c.shadowColor = 'transparent'
          c.shadowBlur = 0
          c.shadowOffsetY = 0
          c.strokeStyle = nd.color
          c.lineWidth = 1.5 / k
          c.stroke()
        }

        c.globalAlpha = alpha
        c.fillStyle = strong ? '#111' : '#555'
        c.textAlign = 'left'
        c.textBaseline = 'middle'
        c.fillText(lb, lx, y)
      }

      // 绘制顺序：先画普通标签占据空位，再画邻居，最后画核心关注节点，
      // 这样「鼠标指到的 / 选中的」标签永远在最上层，不会被普通标签盖住。
      // 注意：这三轮的先后顺序是刻意的，不能合并成一趟——普通标签必须先占位才能被让位，
      // 核心节点的标签必须最后画才能压在最上层。
      if (otherLabels) {
        for (let i = arr.length - 1; i >= 0; i--) {
          if (drawn >= MAX_LABELS) break
          const nd = arr[i]
          if (isRelatedNode(nd)) continue
          drawLabel(nd)
        }
      }

      // 核心节点的邻居：撞位也画，保证「关联节点的标签」一个都不缺
      for (let i = arr.length - 1; i >= 0; i--) {
        if (drawn >= MAX_LABELS) break
        const nd = arr[i]
        if (isCoreNode(nd)) continue
        if (isRelatedNode(nd)) drawLabel(nd, true)
      }

      // 核心关注节点：鼠标指到的、聚焦的、选中的、悬停关系的两端
      for (let i = arr.length - 1; i >= 0; i--) {
        if (drawn >= MAX_LABELS) break
        const nd = arr[i]
        if (isCoreNode(nd)) drawLabel(nd, true)
      }

      c.globalAlpha = 1
    },
    [],
  )

  // ── 小地图绘制 ──────────────────────────────────────────────────────
  /** 在小地图 Canvas 上绘制缩略图和视口矩形 */
  const drawMinimap = useCallback(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const mc = canvas.parentElement?.querySelector('[data-minimap]')
    if (!mc || !nodesRef.current.length) return

    const dpr = window.devicePixelRatio || 1
    mc.width = MINIMAP_W * dpr
    mc.height = MINIMAP_H * dpr
    mc.style.width = MINIMAP_W + 'px'
    mc.style.height = MINIMAP_H + 'px'
    const mctx = mc.getContext('2d')
    mctx.resetTransform()
    mctx.scale(dpr, dpr)
    mctx.clearRect(0, 0, MINIMAP_W, MINIMAP_H)

    let minX = Infinity,
      maxX = -Infinity,
      minY = Infinity,
      maxY = -Infinity
    nodesRef.current.forEach((n) => {
      if (n.x < minX) minX = n.x
      if (n.x > maxX) maxX = n.x
      if (n.y < minY) minY = n.y
      if (n.y > maxY) maxY = n.y
    })
    const pad = 40
    minX -= pad
    minY -= pad
    maxX += pad
    maxY += pad
    const scaleX = MINIMAP_W / (maxX - minX || 1)
    const scaleY = MINIMAP_H / (maxY - minY || 1)
    const scale = Math.min(scaleX, scaleY)
    const ox = (MINIMAP_W - (maxX - minX) * scale) / 2
    const oy = (MINIMAP_H - (maxY - minY) * scale) / 2
    const mapX = (x) => ox + (x - minX) * scale
    const mapY = (y) => oy + (y - minY) * scale

    mctx.globalAlpha = 0.3
    edgesRef.current.forEach((e) => {
      const s = e.source
      const t = e.target
      if (!s?.x || !t?.x) return
      mctx.beginPath()
      mctx.moveTo(mapX(s.x), mapY(s.y))
      mctx.lineTo(mapX(t.x), mapY(t.y))
      mctx.strokeStyle = '#aaa'
      mctx.lineWidth = 0.5
      mctx.stroke()
    })
    mctx.globalAlpha = 1
    nodesRef.current.forEach((n) => {
      mctx.beginPath()
      mctx.arc(mapX(n.x ?? 0), mapY(n.y ?? 0), 2, 0, Math.PI * 2)
      mctx.fillStyle = n.color || '#999'
      mctx.fill()
    })

    // 视口矩形：在小地图上标注当前可视区域
    const t = transformRef.current
    const vx0 = -t.x / t.k
    const vy0 = -t.y / t.k
    const vx1 = (W.current - t.x) / t.k
    const vy1 = (H.current - t.y) / t.k
    const vp = canvas.parentElement?.querySelector('[data-minimap-viewport]')
    if (vp) {
      vp.style.left = mapX(vx0) + 'px'
      vp.style.top = mapY(vy0) + 'px'
      vp.style.width = Math.max(4, (vx1 - vx0) * scale) + 'px'
      vp.style.height = Math.max(4, (vy1 - vy0) * scale) + 'px'
    }
  }, [canvasRef])

  /**
   * 计算可见节点的世界坐标包围盒（含节点半径）。
   * @param {Set<string>|null} vis 可见节点 id 集合，null 表示不过滤
   * @returns {{minX:number,minY:number,maxX:number,maxY:number}|null} 无有效节点时返回 null
   */
  const computeBounds = useCallback((vis) => {
    let minX = Infinity
    let minY = Infinity
    let maxX = -Infinity
    let maxY = -Infinity
    nodesRef.current.forEach((nd) => {
      if (!Number.isFinite(nd.x) || !Number.isFinite(nd.y)) return
      if (vis && !vis.has(nd.id)) return
      const r = nd.r ?? 10
      if (nd.x - r < minX) minX = nd.x - r
      if (nd.x + r > maxX) maxX = nd.x + r
      if (nd.y - r < minY) minY = nd.y - r
      if (nd.y + r > maxY) maxY = nd.y + r
    })
    if (!Number.isFinite(minX)) return null
    return { minX, minY, maxX, maxY }
  }, [])

  /** 由包围盒求解「恰好容纳全部内容」的视图变换（含留白与缩放上下限） */
  const boundsToTransform = useCallback((b, maxZoom = FIT_MAX_ZOOM) => {
    const w = W.current
    const h = H.current
    const bw = Math.max(1, b.maxX - b.minX) + FIT_PADDING * 2
    const bh = Math.max(1, b.maxY - b.minY) + FIT_PADDING * 2
    const k = Math.min(maxZoom, Math.max(ZOOM_MIN, Math.min(w / bw, h / bh)))
    const cx = (b.minX + b.maxX) / 2
    const cy = (b.minY + b.maxY) / 2
    return { x: w / 2 - cx * k, y: h / 2 - cy * k, k }
  }, [])

  // ── 绘制帧 ─────────────────────────────────────────────────────────
  /** 单帧绘制：清空画布 → 应用变换 → 绘制关系 → 绘制节点 → 绘制小地图 */
  const drawFrame = useCallback(() => {
    needRender.current = false
    const c = ctxRef.current
    if (!c) return

    const snap = storeSnap.current

    // 可见集合：本帧仅计算一次，供密度统计、drawEdges/drawNodes 复用
    const vis = new Set()
    graphNodesRef.current.forEach((n) => {
      if (passFilter(n)) vis.add(n.id)
    })


    const k = transformRef.current.k
    c.clearRect(0, 0, W.current, H.current)
    c.save()
    c.translate(transformRef.current.x, transformRef.current.y)
    c.scale(k, k)

    const vx0 = -transformRef.current.x / k - 100
    const vy0 = -transformRef.current.y / k - 100
    const vx1 = (W.current - transformRef.current.x) / k + 100
    const vy1 = (H.current - transformRef.current.y) / k + 100

    const fn = snap.hoveredNodeId
    const fe = hoveredEdgeIdx.current
    const sn = snap.selectedNodeId

    const hasH = fn !== null || fe >= 0
    const focusN = fn || (hasH ? null : sn)
    const ae = fe >= 0 ? fe : -1
    const feObj = ae >= 0 ? (edgesRef.current[ae] ?? null) : null
    const feIds = feObj
      ? new Set([feObj.source?.id, feObj.target?.id].filter(Boolean))
      : null

    // ── LOD 解析：缩放等级 + 视口密度（稀疏图更早显示细节，密集图更早退化为点云）
    let vpCount = 0
    sortedNodesRef.current.forEach((nd) => {
      if (!vis.has(nd.id)) return
      const x = nd.x
      const y = nd.y
      if (!Number.isFinite(x) || !Number.isFinite(y)) return
      if (x < vx0 || x > vx1 || y < vy0 || y > vy1) return
      vpCount++
    })
    const areaK = Math.max(1, ((vx1 - vx0) * (vy1 - vy0)) / 1000)
    const density = vpCount / areaK
    const now = performance.now()
    // LOD 节流：节点移动会让视口密度逐帧抖动，若不限流等级会在相邻两级之间反复跳，
    // 表现为标签忽有忽无（闪）。强制等级至少保持 LOD_CHANGE_MIN_MS 不变。
    const computedLod = resolveLodIndex(k, density, lodStateRef.current.idx)
    let lod = computedLod
    if (computedLod !== lodStateRef.current.idx) {
      if (now - lodStateRef.current.at < LOD_CHANGE_MIN_MS) {
        lod = lodStateRef.current.idx
      } else {
        lodStateRef.current.idx = computedLod
        lodStateRef.current.at = now
      }
    }

    const reduced = nodesRef.current.length > REDUCED_NODE_COUNT
    // 标签占位网格：格子边长取一个标签高度，冲突的标签自动让位
    const grid = lod >= 1 ? createLabelGrid(LOD_STYLE[lod].labelCell / k) : null

    animatingRef.current = false
    drawEdges(c, vx0, vy0, vx1, vy1, k, focusN, feObj, ae, vis, lod)
    drawNodes(c, vx0, vy0, vx1, vy1, k, focusN, feObj, feIds, sn, vis, lod, grid, now, reduced)
    drawEdgeLabels(c, k, lod, focusN, feObj, ae, vis, grid)

    // 建边预览：源节点到光标的蓝色虚线箭头
    if (snap.edgeCreating && snap.edgeMouseWorld) {
      const srcNode = nodesRef.current.find((n) => n.id === snap.edgeSourceId)
      const mw = snap.edgeMouseWorld
      if (srcNode && Number.isFinite(mw.x)) {
        c.save()
        c.setLineDash([6 / k, 4 / k])
        c.strokeStyle = '#3b82f6'
        c.lineWidth = 2 / k
        c.globalAlpha = 0.8
        c.beginPath()
        c.moveTo(srcNode.x ?? 0, srcNode.y ?? 0)
        c.lineTo(mw.x, mw.y)
        c.stroke()
        // 箭头
        const angle = Math.atan2(mw.y - (srcNode.y ?? 0), mw.x - (srcNode.x ?? 0))
        const sz = 10 / k
        c.setLineDash([])
        c.fillStyle = '#3b82f6'
        c.beginPath()
        c.moveTo(mw.x, mw.y)
        c.lineTo(mw.x - sz * Math.cos(angle - 0.4), mw.y - sz * Math.sin(angle - 0.4))
        c.lineTo(mw.x - sz * Math.cos(angle + 0.4), mw.y - sz * Math.sin(angle + 0.4))
        c.closePath()
        c.fill()
        c.restore()
      }
      // 目标节点高亮：蓝色脉冲光晕
      if (snap.edgeTargetId) {
        const tgtNode = nodesRef.current.find((n) => n.id === snap.edgeTargetId)
        if (tgtNode && Number.isFinite(tgtNode.x)) {
          const tr = tgtNode.r ?? 10
          c.save()
          c.globalAlpha = 0.35
          c.strokeStyle = '#3b82f6'
          c.lineWidth = 3 / k
          c.beginPath()
          c.arc(tgtNode.x, tgtNode.y, tr + 6 / k, 0, Math.PI * 2)
          c.stroke()
          c.globalAlpha = 0.12
          c.fillStyle = '#3b82f6'
          c.beginPath()
          c.arc(tgtNode.x, tgtNode.y, tr + 10 / k, 0, Math.PI * 2)
          c.fill()
          c.restore()
        }
      }
    }

    c.restore()

    if (showMinimap) drawMinimap()

    // 入场动画未播完时，仿真可能已停止，需要自行保活下一帧
    if (animatingRef.current && !needRender.current) {
      needRender.current = true
      rafId.current = requestAnimationFrame(() => {
        try {
          drawFrameRef.current()
        } catch (e) {
          console.error('drawFrame error:', e)
        }
      })
    }
  }, [
    drawEdges,
    drawNodes,
    drawEdgeLabels,
    drawMinimap,
    showMinimap,
    passFilter,
  ])

  // ── 调度渲染 ──────────────────────────────────────────────────────
  /** 使用 requestAnimationFrame 调度下一帧渲染，避免重复调度 */
  // 用 ref 转发最新的 drawFrame，供 rAF 调度与动画保活调用。
  // drawFrame 内部会写多个 ref，React Compiler 的 immutability 规则会误判这里的赋值，故显式豁免。
  const drawFrameRef = useRef(drawFrame)
  // eslint-disable-next-line react-hooks/immutability
  useEffect(() => { drawFrameRef.current = drawFrame }, [drawFrame])

  const scheduleRender = useCallback(() => {
    if (needRender.current) return
    needRender.current = true
    rafId.current = requestAnimationFrame(() => {
      try {
        drawFrameRef.current()
      } catch (e) {
        console.error('drawFrame error:', e)
      }
    })
  }, [])

  // ── 相机：缓动与适配视图 ─────────────────────────────────────────────
  /** 取消进行中的相机缓动动画 */
  const cancelCameraAnim = useCallback(() => {
    if (cameraAnimRef.current) {
      cancelAnimationFrame(cameraAnimRef.current)
      cameraAnimRef.current = null
    }
  }, [])

  /**
   * 缓动到目标视图变换。
   * 对 x / y / k 做同步插值（而非 d3.interpolateZoom 的「先缩后移」），观感更稳；
   * 动画期间用户一旦操作视图（userOverrodeCameraRef）立即放弃本次动画。
   */
  const animateTo = useCallback(
    (target, duration = FIT_DURATION) => {
      cancelCameraAnim()
      if (!duration || duration <= 0) {
        transformRef.current = d3.zoomIdentity.translate(target.x, target.y).scale(target.k)
        setZoom(target.k)
        scheduleRender()
        return
      }
      const from = {
        x: transformRef.current.x,
        y: transformRef.current.y,
        k: transformRef.current.k,
      }
      const start = performance.now()
      const step = (now) => {
        if (userOverrodeCameraRef.current) {
          cameraAnimRef.current = null
          return
        }
        const p = Math.min(1, (now - start) / duration)
        const e = d3.easeCubicOut(p)
        transformRef.current = d3.zoomIdentity
          .translate(from.x + (target.x - from.x) * e, from.y + (target.y - from.y) * e)
          .scale(from.k + (target.k - from.k) * e)
        scheduleRender()
        if (p < 1) {
          cameraAnimRef.current = requestAnimationFrame(step)
        } else {
          cameraAnimRef.current = null
          setZoom(target.k)
        }
      }
      cameraAnimRef.current = requestAnimationFrame(step)
    },
    [cancelCameraAnim, scheduleRender, setZoom],
  )


  /**
   * 将视口适配到当前可见节点的包围盒（整图框选）。
   * @param {object} [opts]
   * @param {number} [opts.duration] 动画时长（毫秒），0 表示立即生效
   * @param {number} [opts.maxZoom] 允许的最大放大倍数
   * @param {boolean} [opts.fromUser] 是否由用户主动触发，会重置「用户接管」标记
   * @returns {boolean} 是否成功适配（无可见节点时返回 false）
   */
  const fitView = useCallback(
    (opts = {}) => {
      const { duration = FIT_DURATION, maxZoom = FIT_MAX_ZOOM, fromUser = false } = opts
      if (!W.current || !H.current || !nodesRef.current.length) return false
      if (fromUser) userOverrodeCameraRef.current = false
      const vis = new Set()
      graphNodesRef.current.forEach((n) => {
        if (passFilter(n)) vis.add(n.id)
      })
      const b = computeBounds(vis)
      if (!b) return false
      animateTo(boundsToTransform(b, maxZoom), duration)
      return true
    },
    [animateTo, boundsToTransform, computeBounds, passFilter],
  )

  // 以 ref 暴露 fitView，供只在挂载时注册的 effect 调用（避免闭包陈旧）
  const fitViewRef = useRef(fitView)
  useEffect(() => {
    fitViewRef.current = fitView
  }, [fitView])

  /**
   * 统一管理仿真热度。
   * 新增数据、拖拽、筛选变化都经由此入口，避免多处 alphaTarget 互相覆盖。
   * @param {number} target 目标热度（alphaTarget）
   * @param {number} [ms] 热度保持时长，超时后自动归零；0 表示不自动归零
   */
  const heat = useCallback((target, ms = 0) => {
    const sim = simRef.current
    // 用户已固定布局：不因新数据/交互把它唤醒，避免辛苦调好的位置被重新打散
    if (layoutPinnedRef.current) return
    if (!sim) return
    if (heatTimerRef.current) {
      clearTimeout(heatTimerRef.current)
      heatTimerRef.current = null
    }
    sim.alphaTarget(target).restart()
    if (ms > 0) {
      heatTimerRef.current = setTimeout(() => {
        heatTimerRef.current = null
        simRef.current?.alphaTarget(0)
      }, ms)
    }
  }, [])

  /**
   * 让布局快速收敛并停止（加载完成时调用）。
   * 通过临时调大 alphaDecay 与 alphaMin 加速冷却，约 0.5 秒内达到收敛阈值而自动 stop；
   * 之后若再追加数据，render 会恢复常态参数，不会影响后续布局质量。
   */
  const settleLayout = useCallback(() => {
    const sim = simRef.current
    if (!sim || layoutPinnedRef.current) return
    sim.alphaTarget(0)
    sim.alphaDecay(LAYOUT_SETTLE_DECAY).alphaMin(LAYOUT_SETTLE_MIN)
    // 早先是「设好参数让仿真自己跑完」：万级节点下每 tick 可能数百毫秒，
    // 而 alpha 从 0.3 衰减到收敛阈值需要 30+ 步，于是加载结束后还要晃动几十秒。
    // 改为同步离线快进：在一帧内（受时间预算约束）把布局推到收敛态，随后立即停止。
    sim.stop()
    settlingRef.current = true
    // 快进期间把碰撞迭代降到 1 次：每步便宜 2~3 倍，同样的预算能多推进几步
    const collide = sim.force('collide')
    const prevIterations = collide?.iterations?.()
    collide?.iterations?.(1)
    try {
      const deadline = performance.now() + LAYOUT_SETTLE_BUDGET_MS
      while (sim.alpha() > LAYOUT_SETTLE_MIN && performance.now() < deadline) sim.tick()
    } finally {
      collide?.iterations?.(prevIterations)
      settlingRef.current = false
    }
    // 快进期间跳过了 tick 回调，这里统一收尾
    buildQuadtree()
    scheduleRender()
  }, [buildQuadtree, scheduleRender])

  // ── 缩放到指定比例 ─────────────────────────────────────────────────
  /** 以画布中心为锚点缩放到指定比例 k */
  const zoomTo = useCallback(
    (k) => {
      // 手动缩放同样视为用户接管视图
      userOverrodeCameraRef.current = true
      const cx = W.current / 2
      const cy = H.current / 2
      const tx =
        cx - (cx - transformRef.current.x) * (k / transformRef.current.k)
      const ty =
        cy - (cy - transformRef.current.y) * (k / transformRef.current.k)
      transformRef.current = d3.zoomIdentity.translate(tx, ty).scale(k)
      setZoom(k)
      scheduleRender()
    },
    [setZoom, scheduleRender],
  )

  // ── 聚集孤立节点 ──────────────────────────────────────────────────
  /** 将无连接的孤立节点聚集到画布中心，排列为网格布局 */
  const gatherIsolates = useCallback(() => {
    // 手动整理布局属于用户视图操作，停止自动相机
    userOverrodeCameraRef.current = true
    const isolates = nodesRef.current.filter((n) => n.degree === 0)
    if (!isolates.length) return
    const cols = Math.ceil(Math.sqrt(isolates.length))
    const gap = 60
    const cx = W.current / 2
    const cy = H.current / 2
    isolates.forEach((n, i) => {
      n.x = cx + (i % cols - cols / 2) * gap
      n.y = cy + (Math.floor(i / cols) - Math.floor(isolates.length / cols) / 2) * gap
      n.vx = 0
      n.vy = 0
    })
    const k = 1
    transformRef.current = d3.zoomIdentity.translate(
      cx - cx * k, cy - cy * k
    ).scale(k)
    setZoom(k)
    simRef.current?.alpha(0.3).restart()
    scheduleRender()
  }, [setZoom, scheduleRender])

  // ── 聚焦节点 ──────────────────────────────────────────────────────
  /** 将视口中心移动到指定节点的位置 */
  const focusNode = useCallback(
    (nodeId) => {
      // 手动定位节点属于用户视图操作，停止自动相机
      userOverrodeCameraRef.current = true
      const nd = nodesRef.current.find((n) => n.id === nodeId)
      if (!nd || !Number.isFinite(nd.x)) return
      const k = transformRef.current.k
      const tx = W.current / 2 - nd.x * k
      const ty = H.current / 2 - nd.y * k
      transformRef.current = d3.zoomIdentity.translate(tx, ty).scale(k)
      scheduleRender()
    },
    [scheduleRender],
  )

  // ── 渲染入口 ──────────────────────────────────────────────────────
  /**
   * 主渲染入口：初始化画布 → 数据清洗 → 增量保留旧坐标 → 计算边曲率 →
   * 计算节点度数和半径 → 首次创建或增量更新 D3 力导向仿真
   */
  const render = useCallback(
    (nodes, links) => {
      initCanvas()
      const safeN = (nodes || []).map((n, i) => ({
        ...n,
        id: clean(n.id) || String(i + 1),
      }))
      const byId = new Map(safeN.map((n) => [n.id, n]))
      const safeL = (links || [])
        .filter((l) => {
          const s = clean(typeof l.source === 'object' ? l.source?.id : l.source)
          const t = clean(typeof l.target === 'object' ? l.target?.id : l.target)
          return s && t && byId.has(s) && byId.has(t)
        })
        .map((l) => ({
          ...l,
          source: clean(typeof l.source === 'object' ? l.source?.id : l.source),
          target: clean(typeof l.target === 'object' ? l.target?.id : l.target),
        }))

      // ── 增量追加：保留已有节点的仿真坐标 ───────────────────────────
      const existingMap = new Map(nodesRef.current.map((n) => [n.id, n]))
      safeN.forEach((n) => {
        const p = existingMap.get(n.id)
        if (!p) return
        if (Number.isFinite(p.x)) n.x = p.x
        if (Number.isFinite(p.y)) n.y = p.y
        if (Number.isFinite(p.vx)) n.vx = p.vx
        if (Number.isFinite(p.vy)) n.vy = p.vy
        // 保留未播完的入场动画进度，避免数据刷新时动画被重置
        if (p.appearAt) n.appearAt = p.appearAt
      })

      // ── 新增节点的出生位置 ──────────────────────────────────────────────
      // 优先「从邻居处发芽」：与已有节点有边相连时，直接出生在邻居附近，
      // 力导向几乎不需要收敛就能就位；孤立节点则落在当前视口中心的一圈上，
      // 让加载过程中看到的是「节点在画面里生长」而不是「从屏幕外飞进来」。
      const prevIds = existingMap
      const seeds = new Map() // newId -> [sumX, sumY, count]
      safeL.forEach((l) => {
        const sNew = !prevIds.has(l.source)
        const tNew = !prevIds.has(l.target)
        if (sNew === tNew) return // 两端同为新节点或同为旧节点，不作种子
        const newId = sNew ? l.source : l.target
        const oldId = sNew ? l.target : l.source
        const p = existingMap.get(oldId)
        if (!p || !Number.isFinite(p.x) || !Number.isFinite(p.y)) return
        let sd = seeds.get(newId)
        if (!sd) {
          sd = [0, 0, 0]
          seeds.set(newId, sd)
        }
        sd[0] += p.x
        sd[1] += p.y
        sd[2]++
      })

      const k0 = transformRef.current.k || 1
      const viewCX = (W.current / 2 - transformRef.current.x) / k0
      const viewCY = (H.current / 2 - transformRef.current.y) / k0
      const spawnR = (Math.min(W.current, H.current) * 0.3) / k0
      const birthBase = performance.now()
      let ringIdx = 0
      let staggerIdx = 0
      safeN.forEach((n) => {
        if (prevIds.has(n.id)) return
        const sd = seeds.get(n.id)
        if (sd && sd[2] > 0) {
          const jitter = 40
          n.x = sd[0] / sd[2] + (Math.random() - 0.5) * jitter
          n.y = sd[1] / sd[2] + (Math.random() - 0.5) * jitter
        } else {
          const a = (ringIdx * 2.39996) % (Math.PI * 2) // 黄金角分布，避免重叠
          n.x = viewCX + Math.cos(a) * spawnR
          n.y = viewCY + Math.sin(a) * spawnR
          ringIdx++
        }
        // 入场动画时间戳：同批节点错峰弹出，形成波浪感
        n.appearAt = birthBase + Math.min(staggerIdx * ENTER_STAGGER, ENTER_STAGGER_MAX)
        staggerIdx++
      })

      graphNodesRef.current = safeN
      graphLinksRef.current = safeL
      nodeMapRef.current = new Map(safeN.map((n) => [n.id, n]))

      // 计算边的曲率：同一对节点间多条关系使用不同曲率避免重叠
      const pairCount = {}
      safeL.forEach((e) => {
        const pk = [e.source, e.target].sort().join('_')
        pairCount[pk] = (pairCount[pk] || 0) + 1
      })
      const pairIdx = {}
      const edges = safeL.map((e) => {
        const pk = [e.source, e.target].sort().join('_')
        const total = pairCount[pk]
        const idx = pairIdx[pk] || 0
        pairIdx[pk] = idx + 1
        let c = 0
        if (total > 1) {
          const r = Math.min(1.2, 0.6 + total * 0.15)
          c = (idx / (total - 1) - 0.5) * r * 2
          if (e.source > e.target) c = -c
        }
        return {
          source: e.source,
          target: e.target,
          name: e.relation || 'RELATED',
          curvature: c,
          isSelfLoop: false,
          pairIndex: idx,
          pairTotal: total,
          raw: e.raw,
        }
      })
      edgesRef.current = edges

      // 计算节点度数和可视化半径；同时构建无向邻接表（排除自环）
      const degMap = {}
      const adj = new Map()
      const ensureAdj = (id) => {
        let s = adj.get(id)
        if (!s) { s = new Set(); adj.set(id, s) }
        return s
      }
      edges.forEach((e) => {
        degMap[e.source] = (degMap[e.source] || 0) + 1
        degMap[e.target] = (degMap[e.target] || 0) + 1
        if (!e.isSelfLoop) {
          ensureAdj(e.source).add(e.target)
          ensureAdj(e.target).add(e.source)
        }
      })
      adjacencyRef.current = adj
      // 将度数挂到原始节点上，使 passFilter 的孤立节点判断为 O(1)
      safeN.forEach((n) => { n.degree = degMap[n.id] || 0 })
      const maxDeg = Math.max(1, ...Object.values(degMap))
      nodesRef.current = safeN.map((n) => ({
        ...n,
        degree: degMap[n.id] || 0,
        r: 6 + ((Math.log1p(degMap[n.id] || 0) / Math.log1p(maxDeg)) * 18),
        name: n.label,
      }))

      // 缓存按权重升序的节点快照，供 drawNodes 直接遍历（避免每帧重排）
      sortedNodesRef.current = [...nodesRef.current].sort(
        (a, b) => (a.weight || 0) - (b.weight || 0),
      )

      // ── 仿真：首次创建或增量更新 ────────────────────────────────
      if (!simRef.current) {
        // 首次：创建 D3 力导向仿真，配置各力模型参数
        simRef.current = d3
          .forceSimulation(nodesRef.current)
          .force(
            'link',
            d3
              .forceLink(edgesRef.current)
              .id((d) => d.id)
              .distance((d) => FORCE_LINK_DIST + ((d.pairTotal || 1) - 1) * 50)
              .iterations(1),
          )
          .force(
            'charge',
            d3.forceManyBody().strength(FORCE_CHARGE).distanceMax(600),
          )
          .force('center', d3.forceCenter(W.current / 2, H.current / 2))
          .force(
            'collide',
            d3.forceCollide((d) => (d.r ?? 10) + FORCE_COLLISION).iterations(3),
          )
          .force('x', d3.forceX(W.current / 2).strength(FORCE_PULL))
          .force('y', d3.forceY(H.current / 2).strength(FORCE_PULL))
          .alphaDecay(LAYOUT_ALPHA_DECAY)
          .alphaMin(LAYOUT_ALPHA_MIN)
          .velocityDecay(0.3)
          .on('tick', () => {
            // 离线快进（settleLayout）期间不必每步都重建四叉树 / 请求重绘，
            // 万级节点下这两件事比一步力学计算还贵；快进结束统一收尾一次即可
            if (settlingRef.current) return
            buildQuadtree()
            scheduleRender()
          })
      } else {
        // 增量：向存活的 simulation 追加节点和边，保留已有布局
        const sim = simRef.current
        // 新增数据意味着布局要重新调整，恢复常态冷却参数（上一轮可能处于快速收敛模式）
        sim.alphaDecay(LAYOUT_ALPHA_DECAY).alphaMin(LAYOUT_ALPHA_MIN)
        sim.nodes(nodesRef.current)
        sim.force('link').links(edgesRef.current)
        // 适度升温：已有节点几乎不动，新节点快速收敛（比原来的 alpha(0.15) 收敛更快）
        heat(SIM_HEAT_NEW, SIM_HEAT_MS)
      }

      buildQuadtree()
      scheduleRender()

      // 首屏适配：第一页数据落位后短暂延迟，等力导向把节点铺开一点再框选全图，
      // 否则包围盒还挤在视口中心，会误放大到很大的比例。
      if (!firstFitDoneRef.current && nodesRef.current.length) {
        firstFitDoneRef.current = true
        if (firstFitTimerRef.current) clearTimeout(firstFitTimerRef.current)
        firstFitTimerRef.current = setTimeout(() => {
          firstFitTimerRef.current = null
          fitViewRef.current?.({ duration: FIT_DURATION })
        }, FIRST_FIT_DELAY)
      }
    },
    [initCanvas, buildQuadtree, scheduleRender, heat],
  )

  // ── 指针事件处理器 ─────────────────────────────────────────────────
  /** 鼠标滚轮事件：以光标位置为锚点进行缩放 */
  const onWheel = useCallback(
    (e) => {
      e.preventDefault()
      // 用户主动缩放：立即放弃自动相机并永久接管视图
      userOverrodeCameraRef.current = true
      cancelCameraAnim()
      const canvas = canvasRef.current
      if (!canvas) return
      const r = canvas.getBoundingClientRect()
      const mx = e.clientX - r.left
      const my = e.clientY - r.top
      const d = -e.deltaY * (e.deltaMode === 1 ? 30 : 1)
      const f = Math.pow(1.001, d)
      const nk = Math.min(
        ZOOM_MAX,
        Math.max(ZOOM_MIN, transformRef.current.k * f),
      )
      const tx =
        mx - (mx - transformRef.current.x) * (nk / transformRef.current.k)
      const ty =
        my - (my - transformRef.current.y) * (nk / transformRef.current.k)
      transformRef.current = d3.zoomIdentity.translate(tx, ty).scale(nk)
      setZoom(nk)
      scheduleRender()
    },
    [canvasRef, setZoom, scheduleRender, cancelCameraAnim],
  )

  /** 指针移动事件：处理节点拖拽、画布平移、悬停检测 */
  const onPtrMove = useCallback(
    (e) => {
      const canvas = canvasRef.current
      if (!canvas) return

      // 建边拖拽：更新鼠标坐标和悬停目标节点
      if (storeSnap.current.edgeCreating) {
        const [mwx, mwy] = toWorld(e.clientX, e.clientY)
        setEdgeMouseWorld({ x: mwx, y: mwy })
        const tgt = findNodeAt(mwx, mwy)
        const srcId = storeSnap.current.edgeSourceId
        setEdgeTargetId(tgt && tgt.id !== srcId ? tgt.id : null)
        canvas.style.cursor = tgt ? 'copy' : 'crosshair'
        scheduleRender()
        return
      }

      // 拖拽节点：将鼠标位置转换为世界坐标并更新节点固定位置
      if (dragging.current) {
        const [wx, wy] = toWorld(e.clientX, e.clientY)
        const dx = wx - dragWX.current
        const dy = wy - dragWY.current
        if (!isDrag.current && Math.sqrt(dx * dx + dy * dy) > DRAG_THRESHOLD) {
          isDrag.current = true
          heat(0.3)
        }
        if (isDrag.current) {
          dragging.current.fx = wx
          dragging.current.fy = wy
          // 固定布局时仿真是停的，没有 tick 会把 fx/fy 同步到 x/y，
          // 于是拖拽会「拖不动」；这里直接把节点钉到光标处并重绘，保证始终跟手。
          // （只移动被拖的这个节点，不会把整张图重新搅动，符合「固定布局」的预期）
          if (layoutPinnedRef.current) {
            dragging.current.x = wx
            dragging.current.y = wy
            scheduleRender()
          }
        }
        return
      }

      // 画布平移：更新变换矩阵偏移量
      if (panning.current) {
        transformRef.current = d3.zoomIdentity
          .translate(
            panTX.current + (e.clientX - panX.current),
            panTY.current + (e.clientY - panY.current),
          )
          .scale(transformRef.current.k)
        scheduleRender()
        return
      }

      // 悬停检测：查找光标处的节点和边，更新高亮状态
      const [wx, wy] = toWorld(e.clientX, e.clientY)
      const nd = findNodeAt(wx, wy)
      const snap = storeSnap.current
      // 边只在「未命中节点且没有选中节点」时参与悬停检测（与原有语义一致）
      const edgeIdx = !nd && !snap.selectedNodeId ? findEdgeAt(wx, wy) : -1
      // 命中目标的身份键：只有「在同一目标上停留够久」才真正触发悬浮聚焦。
      // 否则鼠标在画布上一划，沿途节点会被逐个点亮，画面忽明忽暗。
      const hoverKey = nd ? `n:${nd.id}` : edgeIdx >= 0 ? `e:${edgeIdx}` : null

      // 光标形状即时反馈，不受停留时长影响
      canvas.style.cursor = nd || edgeIdx >= 0 ? 'pointer' : 'default'

      /** 真正应用悬停状态（停留满足后才调用） */
      const applyHover = (targetNode, targetEdge) => {
        const cur = storeSnap.current
        const prevHover = cur.hoveredNodeId
        const prevEdge = hoveredEdgeIdx.current
        // 记录原始悬停节点：即使它被下面「选中后仅关注邻居」的逻辑忽略，
        // 「指到哪个节点就能看到它的名字」这一诉求也必须成立
        hoveredRawRef.current = targetNode ? targetNode.id : null

        let hoverAccepted = false
        if (
          cur.selectedNodeId &&
          targetNode &&
          targetNode.id !== cur.selectedNodeId &&
          !isNeighbor(targetNode.id, cur.selectedNodeId)
        ) {
          setHoveredNode(null)
        } else {
          setHoveredNode(targetNode ? targetNode.id : null)
          hoverAccepted = !!targetNode
        }
        hoveredEdgeIdx.current = targetEdge

        const newHoveredNodeId =
          cur.selectedNodeId && !targetNode
            ? cur.selectedNodeId
            : targetNode
              ? targetNode.id
              : null

        if (newHoveredNodeId !== prevHover || hoveredEdgeIdx.current !== prevEdge) {
          if (hoverAccepted) {
            const n = nodesRef.current.find((x) => x.id === targetNode.id)
            if (n) setDetailNode(n)
          } else if (cur.selectedNodeId) {
            const n = nodesRef.current.find((x) => x.id === cur.selectedNodeId)
            if (n) setDetailNode(n)
          } else {
            setDetailNode(null)
          }
          scheduleRender()
        }
      }

      if (hoverKey !== hoverPendingRef.current) {
        hoverPendingRef.current = hoverKey
        if (hoverTimerRef.current) {
          clearTimeout(hoverTimerRef.current)
          hoverTimerRef.current = null
        }
        // 离开原目标：立即撤销高亮——「移开迅速去除」。
        // 若只在计时器里处理，从节点 A 划到相邻节点 B 时 A 的高亮会残留到 B 计时结束才切换。
        applyHover(null, -1)
        if (hoverKey) {
          // 新目标需重新累积停留时长，中途划走不会点亮
          hoverTimerRef.current = setTimeout(() => {
            hoverTimerRef.current = null
            applyHover(nd, edgeIdx)
          }, HOVER_DELAY_MS)
        }
      }
    },
    [
      canvasRef,
      toWorld,
      findNodeAt,
      findEdgeAt,
      isNeighbor,
      setHoveredNode,
      setDetailNode,
      setEdgeMouseWorld,
      setEdgeTargetId,
      scheduleRender,
      heat,
    ],
  )

  /** 指针按下事件：判断是节点拖拽还是画布平移的起点 */
  const onPtrDown = useCallback(
    (e) => {
      if (e.button !== 0) return
      downX.current = e.clientX
      // 用户按下指针：同样视为接管视图，停止自动相机
      userOverrodeCameraRef.current = true
      cancelCameraAnim()
      downY.current = e.clientY
      const canvas = canvasRef.current
      if (!canvas) return
      const [wx, wy] = toWorld(e.clientX, e.clientY)
      const nd = findNodeAt(wx, wy)
      const snap = storeSnap.current

      if (nd && !snap.selectedNodeId) {
        if (e.shiftKey) {
          canvas.setPointerCapture(e.pointerId)
          setEdgeCreating(true)
          setEdgeSourceId(nd.id)
          setEdgeMouseWorld({ x: wx, y: wy })
          setEdgeTargetId(null)
          canvas.style.cursor = 'crosshair'
          return
        }
        canvas.setPointerCapture(e.pointerId)
        dragging.current = nd
        dragWX.current = wx
        dragWY.current = wy
        isDrag.current = false
        nd.fx = nd.x
        nd.fy = nd.y
      } else {
        canvas.setPointerCapture(e.pointerId)
        panning.current = true
        panX.current = e.clientX
        panY.current = e.clientY
        panTX.current = transformRef.current.x
        panTY.current = transformRef.current.y
        canvas.style.cursor = 'grabbing'
      }
    },
    [canvasRef, toWorld, findNodeAt, setEdgeCreating, setEdgeSourceId, setEdgeMouseWorld, setEdgeTargetId, cancelCameraAnim],
  )

  /** 指针释放事件：结束拖拽或平移操作 */
  const onPtrUp = useCallback(() => {
    // 建边模式：释放到目标节点上则弹浮窗，否则取消
    if (storeSnap.current.edgeCreating) {
      const canvas = canvasRef.current
      const tgtId = storeSnap.current.edgeTargetId
      if (tgtId) {
        const t = transformRef.current
        const tgtNode = nodesRef.current.find((n) => n.id === tgtId)
        if (tgtNode) {
          const sx = tgtNode.x * t.k + t.x
          const sy = tgtNode.y * t.k + t.y
          setEdgePopoverPos({ x: sx, y: sy })
        }
      } else {
        setEdgeCreating(false)
        setEdgeSourceId(null)
        setEdgeMouseWorld(null)
        setEdgeTargetId(null)
        if (canvas) canvas.style.cursor = 'default'
      }
      scheduleRender()
      return
    }

    if (dragging.current) {
      if (isDrag.current) heat(0)
      dragging.current.fx = null
      dragging.current.fy = null
      dragging.current = null
      isDrag.current = false
    }
    if (panning.current) {
      panning.current = false
      const canvas = canvasRef.current
      if (canvas) canvas.style.cursor = 'default'
    }
  }, [canvasRef, setEdgePopoverPos, setEdgeCreating, setEdgeSourceId, setEdgeMouseWorld, setEdgeTargetId, scheduleRender, heat])

  /** 指针离开画布：清除悬停状态和光标样式 */
  const onPtrLeave = useCallback(() => {
    // 指针离开画布：作废等待中的悬停计时，避免离开后才突然点亮某个节点
    if (hoverTimerRef.current) {
      clearTimeout(hoverTimerRef.current)
      hoverTimerRef.current = null
    }
    hoverPendingRef.current = null
    if (panning.current) {
      panning.current = false
      const canvas = canvasRef.current
      if (canvas) canvas.style.cursor = 'default'
    }
    hoveredRawRef.current = null
    const snap = storeSnap.current
    if (snap.hoveredNodeId !== null || hoveredEdgeIdx.current !== -1) {
      setHoveredNode(null)
      hoveredEdgeIdx.current = -1
      const canvas = canvasRef.current
      if (canvas) canvas.style.cursor = 'default'
      if (!snap.selectedNodeId) setDetailNode(null)
      scheduleRender()
    }
  }, [canvasRef, setHoveredNode, setDetailNode, scheduleRender])

  /** 点击事件：处理节点选中/取消选中、抓取模式、关系点击、空白点击 */
  const onClick = useCallback(
    (e) => {
      // 忽略拖拽产生的点击（位移超过阈值视为拖拽而非点击）
      if (
        Math.abs(e.clientX - downX.current) > DRAG_THRESHOLD ||
        Math.abs(e.clientY - downY.current) > DRAG_THRESHOLD
      )
        return

      const [wx, wy] = toWorld(e.clientX, e.clientY)
      const nd = findNodeAt(wx, wy)
      const snap = storeSnap.current

      if (nd) {
        // 抓取模式：点击节点后将 nid 写入 store，供表单字段使用
        if (snap.pickTarget) {
          const nid = clean(nd.raw?.nid ?? nd.nid ?? nd.id)
          setPickedNid(nid)
          setStatus(`已抓取节点: ${nid}`)
          return
        }

        if (snap.selectedNodeId === nd.id) {
          // 取消选中：清除所有选中/悬停/详情状态
          setSelectedNode(null)
          setHoveredNode(null)
          setDetailNode(null)
        } else {
          setSelectedNode(nd.id)
          setHoveredNode(nd.id)
          setDetailNode(nd)
          focusNode(nd.id)
        }
        scheduleRender()
        return
      }

      // 点击关系边：显示关系详情
      const ei = findEdgeAt(wx, wy)
      if (ei >= 0) {
        const edge = edgesRef.current[ei]
        const isHighlighted =
          !snap.selectedNodeId ||
          edge.source?.id === snap.selectedNodeId ||
          edge.target?.id === snap.selectedNodeId
        if (isHighlighted && edge.raw) {
          // 将关系原始数据写入详情面板的 store 槽位
          setDetailNode({ _type: 'relation', raw: edge.raw })
          scheduleRender()
          return
        }
      }

      // 点击空白区域：清除所有选中状态
      setSelectedNode(null)
      setHoveredNode(null)
      setDetailNode(null)
      scheduleRender()
    },
    [
      toWorld,
      findNodeAt,
      findEdgeAt,
      focusNode,
      setSelectedNode,
      setHoveredNode,
      setDetailNode,
      setPickTarget,
      setPickedNid,
      setStatus,
      scheduleRender,
    ],
  )

  /** 右键点击节点：弹出上下文菜单 */
  const onContextMenu = useCallback(
    (e) => {
      e.preventDefault()
      const [wx, wy] = toWorld(e.clientX, e.clientY)
      const nd = findNodeAt(wx, wy)
      if (!nd) {
        useAppStore.getState().setContextMenu(null)
        return
      }
      const nid = clean(nd.raw?.nid ?? nd.nid ?? nd.id)
      const nodeName = clean(nd.raw?.name ?? nd.name ?? nid)
      useAppStore.getState().setContextMenu({
        x: e.clientX,
        y: e.clientY,
        nodeId: nd.id,
        nid,
        nodeName,
      })
    },
    [toWorld, findNodeAt],
  )

  // ── 键盘事件（ESC 退出抓取模式）──────────────────────────────────────
  const onKeyDown = useCallback(
    (e) => {
      if (e.key === 'Escape') {
        if (storeSnap.current.edgeCreating) {
          setEdgeCreating(false)
          setEdgeSourceId(null)
          setEdgeMouseWorld(null)
          setEdgeTargetId(null)
          setEdgePopoverPos(null)
          setStatus('已取消建边')
          const canvas = canvasRef.current
          if (canvas) canvas.style.cursor = 'default'
          scheduleRender()
          return
        }
        if (storeSnap.current.pickTarget) {
          setPickTarget(null)
          setStatus('Pick mode cancelled')
        }
      }
    },
    [setPickTarget, setStatus, setEdgeCreating, setEdgeSourceId,
      setEdgeMouseWorld, setEdgeTargetId, setEdgePopoverPos, scheduleRender, canvasRef],
  )

  // ── 窗口尺寸变化处理 ─────────────────────────────────────────────────
  /** 窗口 resize 时重新初始化画布并更新力导向仿真中心点 */
  const onResize = useCallback(() => {
    initCanvas()
    if (simRef.current) {
      simRef.current
        .force('center', d3.forceCenter(W.current / 2, H.current / 2))
        .alpha(0.25)
    }
    scheduleRender()
  }, [initCanvas, scheduleRender])

  // ── 图谱数据变化时重新渲染 ──────────────────────────────────────────
  // ── 加载状态变化：起沿重置自动相机，止沿把整图精确框进视口 ──────────────
  useEffect(() => {
    const was = prevLoadingRef.current
    prevLoadingRef.current = isLoadingData
    if (!was && isLoadingData) {
      // 新一轮加载开始：允许自动相机重新生效，首屏适配重新计算
      firstFitDoneRef.current = false
      userOverrodeCameraRef.current = false
      return
    }
    if (was && !isLoadingData) {
      // 数据全部就位：让布局快速收敛并停下，避免加载完成后节点继续漂移数秒
      settleLayout()
      if (!userOverrodeCameraRef.current) {
        // 等权重计算后的数据落地再收尾，避免框到中间态
        const t = setTimeout(() => {
          fitViewRef.current?.({ duration: FIT_DURATION })
        }, 60)
        return () => clearTimeout(t)
      }
    }
  }, [isLoadingData, settleLayout])

  // ── 固定布局开关：一键冻结/解冻力导向仿真 ──────────────────────────────
  useEffect(() => {
    layoutPinnedRef.current = layoutPinned
    const sim = simRef.current
    if (!sim) return
    if (layoutPinned) {
      // 固定：立即停止仿真，节点位置就此锁死（仍可拖拽单个节点微调）
      sim.alphaTarget(0).stop()
      scheduleRender()
    } else {
      // 解冻：恢复常态冷却参数并重新升温，让布局继续自组织
      sim.alphaDecay(LAYOUT_ALPHA_DECAY).alphaMin(LAYOUT_ALPHA_MIN)
      heat(SIM_HEAT_NEW, SIM_HEAT_MS)
    }
  }, [layoutPinned, heat, scheduleRender])

  // ── 加载期间相机跟随：数据更新后把新区域缓动纳入视野 ────────────────────
  // 用有界缓动（而非逐帧阻尼）实现：加载全程只在缓动窗口内重绘，
  // 不会让渲染循环被"软跟随"永久占住；用户一旦操作视图即永久接管。
  useEffect(() => {
    if (!isLoadingData || userOverrodeCameraRef.current) return
    if (cameraAnimRef.current) return // 已有相机动画在跑，等它结束
    // 去抖：连续多页到达时只跟随最后一次
    const t = setTimeout(() => {
      if (userOverrodeCameraRef.current || cameraAnimRef.current) return
      const vis = new Set()
      graphNodesRef.current.forEach((n) => {
        if (passFilter(n)) vis.add(n.id)
      })
      const b = computeBounds(vis)
      if (!b) return
      animateTo(boundsToTransform(b), CAMERA_FOLLOW_DURATION)
    }, 40)
    return () => clearTimeout(t)
  }, [
    storeNodes,
    storeLinks,
    isLoadingData,
    passFilter,
    computeBounds,
    boundsToTransform,
    animateTo,
  ])

  // 使用 ref 缓存 render 函数，避免闭包陈旧同时防止仿真因 render 变化而重建
  const renderRef = useRef(render)
  useEffect(() => { renderRef.current = render }, [render])
  useEffect(() => {
    if (storeNodes.length > 0 || storeLinks.length > 0) {
      renderRef.current(storeNodes, storeLinks)
    }
  }, [storeNodes, storeLinks])

  // ── 响应式状态变化时触发重渲染 ──────────────────────────────────────
  useEffect(() => {
    scheduleRender()
  }, [
    selectedNodeId,
    hoveredNodeId,
    hoverFocus,
    searchKeyword,
    orphanFilter,
    selectedLegendTypes,
    selectedRelTypes,
    pathHighlight,
    showMinimap,
    scheduleRender,
  ])

  // 组件卸载时清理：停止仿真和取消待渲染帧
  useEffect(() => {
    return () => {
      simRef.current?.stop()
      if (cameraAnimRef.current) cancelAnimationFrame(cameraAnimRef.current)
      if (heatTimerRef.current) clearTimeout(heatTimerRef.current)
      if (firstFitTimerRef.current) clearTimeout(firstFitTimerRef.current)
      if (hoverTimerRef.current) clearTimeout(hoverTimerRef.current)
      if (rafId.current) {
        cancelAnimationFrame(rafId.current)
        needRender.current = false
      }
    }
  }, [])

  // ── 绑定/解绑事件监听器 ──────────────────────────────────────────────
  // 依赖 canvasEl（元素本身）而不是 canvasRef：鉴权未通过时 App 渲染的是密码门，
  // canvas 要等 authState 变为 ready 之后才挂载，此时这些 useCallback 的引用都没变，
  // 若只依赖它们，监听器会绑到一个 null 上且永不重试，表现为「悬浮/点击/拖动/缩放全部失效」。
  useEffect(() => {
    const canvas = canvasEl
    if (!canvas) return

    initCanvas()

    canvas.addEventListener('wheel', onWheel, { passive: false })
    canvas.addEventListener('pointermove', onPtrMove)
    canvas.addEventListener('pointerdown', onPtrDown)
    canvas.addEventListener('pointerup', onPtrUp)
    canvas.addEventListener('pointercancel', onPtrUp)
    canvas.addEventListener('pointerleave', onPtrLeave)
    canvas.addEventListener('click', onClick)
    canvas.addEventListener('contextmenu', onContextMenu)
    document.addEventListener('keydown', onKeyDown)
    window.addEventListener('resize', onResize)

    return () => {
      canvas.removeEventListener('wheel', onWheel)
      canvas.removeEventListener('pointermove', onPtrMove)
      canvas.removeEventListener('pointerdown', onPtrDown)
      canvas.removeEventListener('pointerup', onPtrUp)
      canvas.removeEventListener('pointercancel', onPtrUp)
      canvas.removeEventListener('pointerleave', onPtrLeave)
      canvas.removeEventListener('click', onClick)
      canvas.removeEventListener('contextmenu', onContextMenu)
      document.removeEventListener('keydown', onKeyDown)
      window.removeEventListener('resize', onResize)
      simRef.current?.stop()
    }
  }, [
    canvasEl,
    initCanvas,
    onWheel,
    onPtrMove,
    onPtrDown,
    onPtrUp,
    onPtrLeave,
    onClick,
    onContextMenu,
    onKeyDown,
    onResize,
  ])

  // ── 对外暴露的公共 API ─────────────────────────────────────────────
  return {
    render,
    zoomTo,
    focusNode,
    gatherIsolates,
    fitView,
    cancelCameraAnim,
    scheduleRender,
    resize: onResize,
    transform: transformRef,
    nodes: nodesRef,
    edges: edgesRef,
    simulation: simRef,
  }
}
