import { useEffect, useRef, useCallback } from 'react'
import * as d3 from 'd3'
import { useAppStore } from '@/store'
import {
  FORCE_CHARGE,
  FORCE_LINK_DIST,
  FORCE_COLLISION,
  FORCE_PULL,
} from '@/lib/constants'

// ── Constants ────────────────────────────────────────────────────────
const MINIMAP_W = 180
const MINIMAP_H = 130
const ZOOM_MIN = 0.1
const ZOOM_MAX = 4
const HIT_RADIUS = 18
const EDGE_HIT_TH = 8
const DRAG_THRESHOLD = 4

// ── Helper ───────────────────────────────────────────────────────────
function clean(v) {
  return String(v ?? '').trim()
}

// ═══════════════════════════════════════════════════════════════════════
// useGraphCanvas
// Encapsulates ALL canvas rendering, D3 force simulation, and pointer
// interaction logic. Replaces the original GraphCanvas + GraphInteraction
// classes from group_graph.html.
// ═══════════════════════════════════════════════════════════════════════
export default function useGraphCanvas(canvasRef) {
  // ── Store (reactive) ───────────────────────────────────────────────
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

  const setSelectedNode = useAppStore((s) => s.setSelectedNode)
  const setHoveredNode = useAppStore((s) => s.setHoveredNode)
  const setZoom = useAppStore((s) => s.setZoom)
  const setDetailNode = useAppStore((s) => s.setDetailNode)
  const setPickTarget = useAppStore((s) => s.setPickTarget)
  const setStatus = useAppStore((s) => s.setStatus)

  // ── Refs (mutable internal state) ──────────────────────────────────
  const ctxRef = useRef(null)
  const W = useRef(0)
  const H = useRef(0)
  const simRef = useRef(null)
  const nodesRef = useRef([]) // simulation nodes with x/y/vx/vy
  const edgesRef = useRef([]) // processed edges with curvature
  const graphNodesRef = useRef([]) // original node data for filtering
  const graphLinksRef = useRef([]) // original link data for filtering
  const transformRef = useRef(d3.zoomIdentity)
  const quadtreeRef = useRef(null)
  const needRender = useRef(false)
  const rafId = useRef(null)

  // interaction state
  const hoveredEdgeIdx = useRef(-1)
  const dragging = useRef(null)
  const dragWX = useRef(0)
  const dragWY = useRef(0)
  const isDrag = useRef(false)
  const panning = useRef(false)
  const panX = useRef(0)
  const panY = useRef(0)
  const panTX = useRef(0)
  const panTY = useRef(0)
  const downX = useRef(0)
  const downY = useRef(0)

  // cache store snapshot refs so drawing code can read latest without
  // triggering re-render on every keystroke
  const storeSnap = useRef({
    selectedNodeId: null,
    hoveredNodeId: null,
    searchKeyword: '',
    orphanFilter: false,
    selectedLegendTypes: new Set(),
    selectedRelTypes: new Set(),
    pathHighlight: null,
    pickTarget: null,
  })

  // Keep snapshot in sync
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
  ])

  // ── Canvas init ────────────────────────────────────────────────────
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

  // ── Quadtree ───────────────────────────────────────────────────────
  const buildQuadtree = useCallback(() => {
    quadtreeRef.current = d3
      .quadtree()
      .x((d) => d.x ?? 0)
      .y((d) => d.y ?? 0)
      .addAll(nodesRef.current)
  }, [])

  // ── Coordinate helpers ─────────────────────────────────────────────
  const toWorld = useCallback((ex, ey) => {
    const canvas = canvasRef.current
    if (!canvas) return [0, 0]
    const r = canvas.getBoundingClientRect()
    return transformRef.current.invert([ex - r.left, ey - r.top])
  }, [canvasRef])

  // ── Filtering helpers ──────────────────────────────────────────────
  const getNodeLabelKeys = useCallback((n) => {
    const labels = Array.isArray(n?.raw?.labels) ? n.raw.labels : []
    const keys = labels.map((x) => clean(x).toLowerCase()).filter(Boolean)
    return keys.length ? keys : [clean(n?.type || 'Node').toLowerCase()]
  }, [])

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

  const passEdgeFilter = useCallback((edge) => {
    const snap = storeSnap.current
    if (!snap.selectedRelTypes.size) return true
    return snap.selectedRelTypes.has(clean(edge.name).toLowerCase())
  }, [])

  // ── Bezier helpers ─────────────────────────────────────────────────
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

  // ── Hit testing ────────────────────────────────────────────────────
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

  // ── isNeighbor ─────────────────────────────────────────────────────
  const isNeighbor = useCallback((a, b) => {
    return edgesRef.current.some(
      (e) =>
        !e.isSelfLoop &&
        ((e.source?.id === b && e.target?.id === a) ||
          (e.target?.id === b && e.source?.id === a)),
    )
  }, [])

  // ── Draw helpers ───────────────────────────────────────────────────
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
        // viewport culling
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

        // alpha
        let alpha
        if (ph && hasF) {
          alpha = act ? 1 : onPath ? 0.2 : 0.05
        } else if (ph) {
          alpha = onPath ? 1 : 0.08
        } else {
          alpha = hasF ? (act ? 1 : 0.1) : 0.55
        }
        c.globalAlpha = alpha

        // stroke style
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

        // arrow at target
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

        // edge label
        c.globalAlpha = alpha
        if (edge.name) {
          if (ph && hasF && !act) return
          if (ph && !hasF && !onPath) return
          if (!ph && hasF && !act) return
          const m = bezMid(edge)
          const lb =
            edge.name.length > 14 ? edge.name.slice(0, 14) + '…' : edge.name
          const fs = Math.max(9, 9 / k)
          c.font = `${fs}px 'JetBrains Mono',monospace`
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

  const drawNodes = useCallback(
    (c, vx0, vy0, vx1, vy1, k, focusN, feObj, feIds, sn) => {
      const hasF = !!(focusN || feObj)
      const ph = storeSnap.current.pathHighlight
      const vis = new Set()
      graphNodesRef.current.forEach((n) => {
        if (passFilter(n)) vis.add(n.id)
      })

      // sort by weight ascending so heavier nodes are drawn on top
      const sorted = [...nodesRef.current].sort(
        (a, b) => (a.weight || 0) - (b.weight || 0),
      )

      sorted.forEach((nd) => {
        const x = nd.x ?? 0
        const y = nd.y ?? 0
        // viewport culling
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

        // alpha
        if (ph && hasF) {
          c.globalAlpha = fc || isNN ? 1 : onPath ? 0.2 : 0.05
        } else if (ph) {
          c.globalAlpha = onPath ? 1 : 0.1
        } else {
          c.globalAlpha = hasF ? (fc || isNN ? 1 : 0.15) : 1
        }

        // path glow
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

        // selected glow
        if (fc) {
          c.beginPath()
          c.arc(x, y, r + 5 / k, 0, Math.PI * 2)
          c.strokeStyle = nd.color
          c.lineWidth = 2.5 / k
          c.globalAlpha = 0.35
          c.stroke()
          c.globalAlpha = 1
        }

        // circle
        c.beginPath()
        c.arc(x, y, r, 0, Math.PI * 2)
        c.fillStyle = nd.color
        c.fill()
        c.strokeStyle = fc ? '#333' : '#fff'
        c.lineWidth = (fc ? 3 : 2.5) / k
        c.stroke()

        // label
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

  // ── Minimap ────────────────────────────────────────────────────────
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

    // viewport rectangle
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

  // ── Draw frame ─────────────────────────────────────────────────────
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

    c.restore()

    if (showMinimap) drawMinimap()
  }, [drawEdges, drawNodes, drawMinimap, showMinimap])

  // ── Schedule render ────────────────────────────────────────────────
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

  // ── zoomTo ─────────────────────────────────────────────────────────
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

  // ── gatherIsolates (cluster orphan nodes at center, reset zoom) ────
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

  // ── focusNode (center viewport on a node) ──────────────────────────
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

  // ── render (main entry point) ──────────────────────────────────────
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

      // preserve previous positions
      const prev = new Map(nodesRef.current.map((n) => [n.id, n]))
      safeN.forEach((n) => {
        const p = prev.get(n.id)
        if (!p) return
        if (Number.isFinite(p.x)) n.x = p.x
        if (Number.isFinite(p.y)) n.y = p.y
        if (Number.isFinite(p.vx)) n.vx = p.vx
        if (Number.isFinite(p.vy)) n.vy = p.vy
      })

      graphNodesRef.current = safeN
      graphLinksRef.current = safeL

      // compute edge curvature
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

      // compute degrees and radius
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

      // stop old simulation
      simRef.current?.stop()

      // create new simulation
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

      buildQuadtree()
      scheduleRender()
    },
    [initCanvas, buildQuadtree, scheduleRender],
  )

  // ── Pointer event handlers ─────────────────────────────────────────
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

  const onPtrMove = useCallback(
    (e) => {
      const canvas = canvasRef.current
      if (!canvas) return

      // dragging a node
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

      // panning
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

      // hover detection
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
      scheduleRender,
    ],
  )

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
    [canvasRef, toWorld, findNodeAt],
  )

  const onPtrUp = useCallback(() => {
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
  }, [canvasRef])

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

  const onClick = useCallback(
    (e) => {
      // ignore if it was a drag
      if (
        Math.abs(e.clientX - downX.current) > DRAG_THRESHOLD ||
        Math.abs(e.clientY - downY.current) > DRAG_THRESHOLD
      )
        return

      const [wx, wy] = toWorld(e.clientX, e.clientY)
      const nd = findNodeAt(wx, wy)
      const snap = storeSnap.current

      if (nd) {
        // pick mode
        if (snap.pickTarget) {
          setPickTarget(null)
          setStatus(`Picked node: ${clean(nd.raw?.nid ?? nd.nid ?? nd.id)}`)
          // The caller is responsible for reading the picked nid from the
          // input field that pickTarget was pointing at.
          // We write it directly into the DOM input as the original did.
          const el = document.getElementById(snap.pickTarget)
          if (el) el.value = clean(nd.raw?.nid ?? nd.nid ?? nd.id)
          return
        }

        if (snap.selectedNodeId === nd.id) {
          // deselect
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

      // edge click
      const ei = findEdgeAt(wx, wy)
      if (ei >= 0) {
        const edge = edgesRef.current[ei]
        const isHighlighted =
          !snap.selectedNodeId ||
          edge.source?.id === snap.selectedNodeId ||
          edge.target?.id === snap.selectedNodeId
        if (isHighlighted && edge.raw) {
          // Signal that a relation was clicked — the consumer can listen
          // to the store for this. For parity with the original, we write
          // the raw relation data into the store's detail slot.
          setDetailNode({ _type: 'relation', raw: edge.raw })
          scheduleRender()
          return
        }
      }

      // clicked empty space
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
      setStatus,
      scheduleRender,
    ],
  )

  // ── Keyboard (Escape exits pick mode) ──────────────────────────────
  const onKeyDown = useCallback(
    (e) => {
      if (e.key === 'Escape' && storeSnap.current.pickTarget) {
        setPickTarget(null)
        setStatus('Pick mode cancelled')
      }
    },
    [setPickTarget, setStatus],
  )

  // ── Resize handler ─────────────────────────────────────────────────
  const onResize = useCallback(() => {
    initCanvas()
    if (simRef.current) {
      simRef.current
        .force('center', d3.forceCenter(W.current / 2, H.current / 2))
        .alpha(0.25)
    }
    scheduleRender()
  }, [initCanvas, scheduleRender])

  // ── Re-render when graph data changes ─────────────────────────────
  // Use a ref for render to avoid stale closures while preventing
  // the simulation from being recreated on every render function change.
  const renderRef = useRef(render)
  useEffect(() => { renderRef.current = render }, [render])
  useEffect(() => {
    if (storeNodes.length > 0 || storeLinks.length > 0) {
      renderRef.current(storeNodes, storeLinks)
    }
  }, [storeNodes, storeLinks])

  // ── Re-render when reactive state changes ──────────────────────────
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

  // Cleanup on unmount only
  useEffect(() => {
    return () => {
      simRef.current?.stop()
      if (rafId.current) {
        cancelAnimationFrame(rafId.current)
        needRender.current = false
      }
    }
  }, [])

  // ── Bind / unbind event listeners ──────────────────────────────────
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
    onKeyDown,
    onResize,
  ])

  // ── Public API ─────────────────────────────────────────────────────
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
