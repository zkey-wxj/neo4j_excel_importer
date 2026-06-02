import { useEffect, useRef, useCallback } from 'react'
import * as d3 from 'd3'
import { useAppStore } from '@/store'
import {
  FORCE_CHARGE,
  FORCE_LINK_DIST,
  FORCE_COLLISION,
  FORCE_PULL,
} from '@/lib/constants'

// ── 常量定义 ────────────────────────────────────────────────────────
const MINIMAP_W = 180       // 小地图宽度（像素）
const MINIMAP_H = 130       // 小地图高度（像素）
const ZOOM_MIN = 0.1        // 最小缩放比例
const ZOOM_MAX = 4          // 最大缩放比例
const HIT_RADIUS = 18       // 节点点击检测半径（世界坐标）
const EDGE_HIT_TH = 8       // 关系边点击检测阈值（像素）
const DRAG_THRESHOLD = 4    // 拖拽判定阈值（像素），小于此值视为点击

// ── 辅助函数 ───────────────────────────────────────────────────────
/** 安全的字符串 trim */
function clean(v) {
  return String(v ?? '').trim()
}

// ═══════════════════════════════════════════════════════════════════════
// useGraphCanvas
// 封装了所有的 Canvas 渲染、D3 力导向仿真和指针交互逻辑
// 替代了原始 group_graph.html 中的 GraphCanvas + GraphInteraction 类
// 核心职责：仿真驱动、画布绘制、缩放平移、节点拖拽、命中检测
// ═══════════════════════════════════════════════════════════════════════
export default function useGraphCanvas(canvasRef) {
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
  const needRender = useRef(false) // 是否有待渲染的帧
  const rafId = useRef(null)       // requestAnimationFrame ID

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
    if (snap.orphanFilter) {
      const id = n.id
      const connected = graphLinksRef.current.some((l) => {
        const s = clean(typeof l.source === 'object' ? l.source?.id : l.source)
        const t = clean(typeof l.target === 'object' ? l.target?.id : l.target)
        return s === id || t === id
      })
      if (connected) return false
    }
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
      const g = graphNodesRef.current.find((n) => n.id === nd.id)
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
        const sg = graphNodesRef.current.find((n) => n.id === s.id)
        const tg = graphNodesRef.current.find((n) => n.id === t.id)
        if (!sg || !tg || !passFilter(sg) || !passFilter(tg)) continue
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
    return edgesRef.current.some(
      (e) =>
        !e.isSelfLoop &&
        ((e.source?.id === b && e.target?.id === a) ||
          (e.target?.id === b && e.source?.id === a)),
    )
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
    (c, vx0, vy0, vx1, vy1, k, focusN, feObj, feIds, ae) => {
      const hasF = !!(focusN || feObj)
      const vis = new Set()
      graphNodesRef.current.forEach((n) => {
        if (passFilter(n)) vis.add(n.id)
      })
      const ph = storeSnap.current.pathHighlight

      edgesRef.current.forEach((edge, i) => {
        const s = edge.source
        const t = edge.target
        if (!s?.x || !t?.x) return
        const sx = s.x
        const sy = s.y
        const tx = t.x
        const ty = t.y
        // 视口裁剪：跳过不在可视区域内的边（drawEdges 中）
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
        const isNL =
          focusN &&
          (s.id === focusN || t.id === focusN)
        const act = isE || isNL

        // 透明度计算：根据聚焦状态、路径高亮、悬停状态综合决定
        let alpha
        if (ph && hasF) {
          alpha = act ? 1 : onPath ? 0.2 : 0.05
        } else if (ph) {
          alpha = onPath ? 1 : 0.08
        } else {
          alpha = hasF ? (act ? 1 : 0.1) : 0.55
        }
        c.globalAlpha = alpha

        // 笔触样式：根据路径高亮、激活、悬停状态设置颜色和线宽
        c.beginPath()
        c.lineWidth = onPath ? 2.5 / k : act ? 2.5 / k : 1.6 / k
        c.strokeStyle = onPath
          ? '#d63e52'
          : act
            ? '#3b5bdb'
            : isE
              ? '#5c7cfa'
              : '#C0C0C0'

        if (edge.isSelfLoop) {
          c.arc(sx + 20, sy - 20, 22, 0, Math.PI * 2)
        } else {
          const cp = bezCp(edge)
          c.moveTo(sx, sy)
          if (cp && edge.curvature) c.quadraticCurveTo(cp.cx, cp.cy, tx, ty)
          else c.lineTo(tx, ty)
        }
        c.stroke()

        // 箭头绘制：在目标端点附近绘制方向箭头
        if (!edge.isSelfLoop) {
          const tr = t.r ?? 10
          let ax, ay, angle
          if (edge.curvature) {
            const cp2 = bezCp(edge)
            const u = 0.92
            const w = 1 - u
            ax = w * w * sx + 2 * w * u * cp2.cx + u * u * tx
            ay = w * w * sy + 2 * w * u * cp2.cy + u * u * ty
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
          const sz = Math.max(6, 8 / k)
          c.save()
          c.translate(ax, ay)
          c.rotate(angle)
          c.beginPath()
          c.moveTo(0, 0)
          c.lineTo(-sz, -sz * 0.5)
          c.lineTo(-sz, sz * 0.5)
          c.closePath()
          c.fillStyle = c.strokeStyle
          c.fill()
          c.restore()
        }

        // 关系标签：在边的中点位置绘制类型名称（带圆角背景）
        c.globalAlpha = alpha
        if (edge.name) {
          if (ph && hasF && !act) return
          if (ph && !hasF && !onPath) return
          if (!ph && hasF && !act) return
          const m = bezMid(edge)
          const lb =
            edge.name.length > 14 ? edge.name.slice(0, 14) + '…' : edge.name
          const fs = Math.max(9, 9 / k)
          c.font = `${fs}px ui-monospace, monospace`
          const tw = c.measureText(lb).width
          const th = fs * 1.4
          const hl = isE || isNL
          c.fillStyle = hl
            ? 'rgba(155,89,182,0.12)'
            : 'rgba(255,255,255,0.95)'
          c.beginPath()
          c.roundRect(
            m.x - tw / 2 - 4,
            m.y - th / 2 - 2,
            tw + 8,
            th + 4,
            3 / k,
          )
          c.fill()
          c.fillStyle = isE ? '#5c7cfa' : isNL ? '#3b5bdb' : '#666'
          c.textAlign = 'center'
          c.textBaseline = 'middle'
          c.fillText(lb, m.x, m.y)
        }
      })
    },
    [passFilter, passEdgeFilter, bezCp, bezMid],
  )

  /**
   * 绘制所有节点
   * 包括：视口裁剪、路径/聚焦高亮发光、圆形填充和描边、名称标签
   * 节点按权重升序绘制，权重大的在上层
   */
  const drawNodes = useCallback(
    (c, vx0, vy0, vx1, vy1, k, focusN, feObj, feIds, sn) => {
      const hasF = !!(focusN || feObj)
      const ph = storeSnap.current.pathHighlight
      const vis = new Set()
      graphNodesRef.current.forEach((n) => {
        if (passFilter(n)) vis.add(n.id)
      })

      // 按权重升序排列，确保权重大的节点绘制在上层
      const sorted = [...nodesRef.current].sort(
        (a, b) => (a.weight || 0) - (b.weight || 0),
      )

      sorted.forEach((nd) => {
        const x = nd.x ?? 0
        const y = nd.y ?? 0
        // 视口裁剪：跳过不在可视区域内的节点
        if (x < vx0 || x > vx1 || y < vy0 || y > vy1) return
        if (!vis.has(nd.id)) return

        const onPath = ph && ph.has(nd.id)
        const BR = nd.r ?? 10
        const isNF = nd.id === focusN
        const isNN =
          focusN &&
          !isNF &&
          edgesRef.current.some(
            (e) =>
              !e.isSelfLoop &&
              ((e.source?.id === focusN && e.target?.id === nd.id) ||
                (e.target?.id === focusN && e.source?.id === nd.id)),
          )
        const isEF = feIds?.has(nd.id)
        const isSN = nd.id === sn
        const fc = isNF || isEF || isSN
        const r = BR

        // 透明度计算：路径高亮、聚焦、搜索等状态的综合判断
        if (ph && hasF) {
          c.globalAlpha = fc || isNN ? 1 : onPath ? 0.2 : 0.05
        } else if (ph) {
          c.globalAlpha = onPath ? 1 : 0.1
        } else {
          c.globalAlpha = hasF ? (fc || isNN ? 1 : 0.15) : 1
        }

        // 路径高亮发光效果：红色光晕
        if (onPath && (!ph || !hasF || fc || isNN)) {
          c.beginPath()
          c.arc(x, y, r + 6 / k, 0, Math.PI * 2)
          c.strokeStyle = '#d63e52'
          c.lineWidth = 2.5 / k
          c.globalAlpha = Math.min(c.globalAlpha, 0.5)
          c.stroke()
          c.globalAlpha =
            ph && hasF
              ? fc || isNN
                ? 1
                : 0.2
              : ph
                ? onPath
                  ? 1
                  : 0.1
                : 1
        }

        // 选中/悬停发光效果：使用节点自身颜色的光晕
        if (fc) {
          c.beginPath()
          c.arc(x, y, r + 5 / k, 0, Math.PI * 2)
          c.strokeStyle = nd.color
          c.lineWidth = 2.5 / k
          c.globalAlpha = 0.35
          c.stroke()
          c.globalAlpha = 1
        }

        // 绘制节点圆形：填充 + 描边
        c.beginPath()
        c.arc(x, y, r, 0, Math.PI * 2)
        c.fillStyle = nd.color
        c.fill()
        c.strokeStyle = fc ? '#333' : '#fff'
        c.lineWidth = (fc ? 3 : 2.5) / k
        c.stroke()

        // 节点名称标签：缩放大于 0.6 时显示，聚焦节点显示更长的名称
        if (k > 0.6) {
          const sf = (fc || isNN) && !ph
          const nodeAlpha =
            ph && hasF
              ? fc || isNN
                ? 1
                : onPath
                  ? 0.2
                  : 0.05
              : ph
                ? onPath
                  ? 1
                  : 0.1
                : hasF
                  ? fc || isNN
                    ? 1
                    : 0.15
                  : 1
          const rn = nd.name || nd.label || ''
          const lb = sf
            ? rn.length > 16
              ? rn.slice(0, 16) + '…'
              : rn
            : rn.length > 8
              ? rn.slice(0, 8) + '…'
              : rn
          const fs = sf ? Math.max(12, 12 / k) : Math.max(11, 11 / k)
          const wt = sf ? '600' : '500'
          c.font = `${wt} ${fs}px 'JetBrains Mono',monospace`
          const lx = x + r + 5 / k
          const ly = y

          if (sf) {
            c.globalAlpha = nodeAlpha
            const tw = c.measureText(lb).width
            const pad = 5 / k
            const bh = (fs + 8) / k
            c.shadowColor = 'rgba(0,0,0,0.18)'
            c.shadowBlur = 6 / k
            c.shadowOffsetY = 2 / k
            c.fillStyle = '#fff'
            c.beginPath()
            c.roundRect(lx - pad, ly - bh / 2, tw + pad * 2, bh, bh / 2)
            c.fill()
            c.shadowColor = 'transparent'
            c.shadowBlur = 0
            c.shadowOffsetY = 0
            c.strokeStyle = nd.color
            c.lineWidth = 1.5 / k
            c.stroke()
          }

          c.fillStyle = sf ? '#111' : '#555'
          c.globalAlpha = nodeAlpha
          c.textAlign = 'left'
          c.textBaseline = 'middle'
          c.fillText(lb, lx, ly)
        }

        c.globalAlpha = 1
      })
    },
    [passFilter],
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

  // ── 绘制帧 ─────────────────────────────────────────────────────────
  /** 单帧绘制：清空画布 → 应用变换 → 绘制关系 → 绘制节点 → 绘制小地图 */
  const drawFrame = useCallback(() => {
    needRender.current = false
    const c = ctxRef.current
    const k = transformRef.current.k

    if (!c) return

    c.clearRect(0, 0, W.current, H.current)
    c.save()
    c.translate(transformRef.current.x, transformRef.current.y)
    c.scale(k, k)

    const vx0 = -transformRef.current.x / k - 100
    const vy0 = -transformRef.current.y / k - 100
    const vx1 = (W.current - transformRef.current.x) / k + 100
    const vy1 = (H.current - transformRef.current.y) / k + 100

    const snap = storeSnap.current
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

    drawEdges(c, vx0, vy0, vx1, vy1, k, focusN, feObj, feIds, ae)
    drawNodes(c, vx0, vy0, vx1, vy1, k, focusN, feObj, feIds, sn)

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
  }, [drawEdges, drawNodes, drawMinimap, showMinimap])

  // ── 调度渲染 ──────────────────────────────────────────────────────
  /** 使用 requestAnimationFrame 调度下一帧渲染，避免重复调度 */
  const drawFrameRef = useRef(drawFrame)
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

  // ── 缩放到指定比例 ─────────────────────────────────────────────────
  /** 以画布中心为锚点缩放到指定比例 k */
  const zoomTo = useCallback(
    (k) => {
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
      })

      // 为新增节点设定接近图中心的初始位置，避免从随机位置弹入
      const prevIds = existingMap
      safeN.forEach((n) => {
        if (!prevIds.has(n.id)) {
          n.x = W.current / 2 + (Math.random() - 0.5) * 100
          n.y = H.current / 2 + (Math.random() - 0.5) * 100
        }
      })

      graphNodesRef.current = safeN
      graphLinksRef.current = safeL

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

      // 计算节点度数和可视化半径
      const degMap = {}
      edges.forEach((e) => {
        degMap[e.source] = (degMap[e.source] || 0) + 1
        degMap[e.target] = (degMap[e.target] || 0) + 1
      })
      const maxDeg = Math.max(1, ...Object.values(degMap))
      nodesRef.current = safeN.map((n) => ({
        ...n,
        degree: degMap[n.id] || 0,
        r: 6 + ((Math.log1p(degMap[n.id] || 0) / Math.log1p(maxDeg)) * 18),
        name: n.label,
      }))

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
          .alphaDecay(0.02)
          .velocityDecay(0.3)
          .on('tick', () => {
            buildQuadtree()
            scheduleRender()
          })
      } else {
        // 增量：向存活的 simulation 追加节点和边，保留已有布局
        const sim = simRef.current
        sim.nodes(nodesRef.current)
        sim.force('link').links(edgesRef.current)
        // 低热度重启——已有节点几乎不动，新节点快速收敛
        sim.alpha(0.15).restart()
      }

      buildQuadtree()
      scheduleRender()
    },
    [initCanvas, buildQuadtree, scheduleRender],
  )

  // ── 指针事件处理器 ─────────────────────────────────────────────────
  /** 鼠标滚轮事件：以光标位置为锚点进行缩放 */
  const onWheel = useCallback(
    (e) => {
      e.preventDefault()
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
    [canvasRef, setZoom, scheduleRender],
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
          simRef.current?.alphaTarget(0.3).restart()
        }
        if (isDrag.current) {
          dragging.current.fx = wx
          dragging.current.fy = wy
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
      const prevHover = snap.hoveredNodeId
      const prevEdge = hoveredEdgeIdx.current

      let hoverAccepted = false
      if (
        snap.selectedNodeId &&
        nd &&
        nd.id !== snap.selectedNodeId &&
        !isNeighbor(nd.id, snap.selectedNodeId)
      ) {
        setHoveredNode(null)
      } else {
        setHoveredNode(nd ? nd.id : null)
        hoverAccepted = !!nd
      }

      const newHoveredNodeId =
        snap.selectedNodeId && !nd ? snap.selectedNodeId : nd ? nd.id : null

      if (!nd && !snap.selectedNodeId) {
        hoveredEdgeIdx.current = findEdgeAt(wx, wy)
      } else {
        hoveredEdgeIdx.current = -1
      }

      canvas.style.cursor =
        newHoveredNodeId || hoveredEdgeIdx.current >= 0 ? 'pointer' : 'default'

      if (
        newHoveredNodeId !== prevHover ||
        hoveredEdgeIdx.current !== prevEdge
      ) {
        if (hoverAccepted) {
          const n = nodesRef.current.find((x) => x.id === nd.id)
          if (n) setDetailNode(n)
        } else if (snap.selectedNodeId) {
          const n = nodesRef.current.find((x) => x.id === snap.selectedNodeId)
          if (n) setDetailNode(n)
        } else {
          setDetailNode(null)
        }
        scheduleRender()
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
    ],
  )

  /** 指针按下事件：判断是节点拖拽还是画布平移的起点 */
  const onPtrDown = useCallback(
    (e) => {
      if (e.button !== 0) return
      downX.current = e.clientX
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
    [canvasRef, toWorld, findNodeAt, setEdgeCreating, setEdgeSourceId, setEdgeMouseWorld, setEdgeTargetId],
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
      if (isDrag.current) simRef.current?.alphaTarget(0)
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
  }, [canvasRef, setEdgePopoverPos, setEdgeCreating, setEdgeSourceId, setEdgeMouseWorld, setEdgeTargetId, scheduleRender])

  /** 指针离开画布：清除悬停状态和光标样式 */
  const onPtrLeave = useCallback(() => {
    if (panning.current) {
      panning.current = false
      const canvas = canvasRef.current
      if (canvas) canvas.style.cursor = 'default'
    }
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
      if (rafId.current) {
        cancelAnimationFrame(rafId.current)
        needRender.current = false
      }
    }
  }, [])

  // ── 绑定/解绑事件监听器 ──────────────────────────────────────────────
  useEffect(() => {
    const canvas = canvasRef.current
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
    canvasRef,
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
    scheduleRender,
    resize: onResize,
    transform: transformRef,
    nodes: nodesRef,
    edges: edgesRef,
    simulation: simRef,
  }
}
