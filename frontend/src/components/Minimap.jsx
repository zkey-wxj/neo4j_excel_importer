import { useRef, useEffect, useCallback } from 'react'
import { useAppStore } from '@/store'
import { cn } from '@/lib/utils'

// 小地图的固定宽高（像素）
const MW = 180
const MH = 130

/**
 * 小地图组件
 * 在右下角显示图谱的缩略视图，包含当前视口矩形框，
 * 帮助用户了解当前可视区域在全图中的位置
 */
export default function Minimap({ graphCanvas }) {
  const showMinimap = useAppStore((s) => s.showMinimap)
  const nodes = useAppStore((s) => s.nodes)
  const links = useAppStore((s) => s.links)
  const canvasRef = useRef(null)
  const viewportRef = useRef(null)

  // 稳定的绘制函数：在小地图 Canvas 上绘制所有节点和边
  // 每次绘制前调用 resetTransform() 避免 DPR 缩放在动画帧间累积
  const draw = useCallback(() => {
    const canvas = canvasRef.current
    if (!canvas) return

    const simNodes = nodes || []
    const simEdges = links || []

    if (!simNodes.length) {
      const ctx = canvas.getContext('2d')
      ctx?.resetTransform()
      ctx?.clearRect(0, 0, MW, MH)
      return
    }

    const dpr = window.devicePixelRatio || 1
    canvas.width = MW * dpr
    canvas.height = MH * dpr
    canvas.style.width = MW + 'px'
    canvas.style.height = MH + 'px'
    const ctx = canvas.getContext('2d')
    ctx.resetTransform()
    ctx.scale(dpr, dpr)
    ctx.clearRect(0, 0, MW, MH)

    // 计算所有节点的包围盒，用于坐标映射
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity
    simNodes.forEach((n) => {
      const x = n.x ?? 0
      const y = n.y ?? 0
      if (x < minX) minX = x
      if (x > maxX) maxX = x
      if (y < minY) minY = y
      if (y > maxY) maxY = y
    })

    const pad = 40
    minX -= pad; minY -= pad; maxX += pad; maxY += pad
    const scaleX = MW / (maxX - minX || 1)
    const scaleY = MH / (maxY - minY || 1)
    const scale = Math.min(scaleX, scaleY)
    const ox = (MW - (maxX - minX) * scale) / 2
    const oy = (MH - (maxY - minY) * scale) / 2
    const mapX = (x) => ox + (x - minX) * scale
    const mapY = (y) => oy + (y - minY) * scale

    // 绘制关系边（半透明灰色线条）
    ctx.globalAlpha = 0.3
    simEdges.forEach((e) => {
      const s = e.source
      const t = e.target
      if (!s?.x || !t?.x) return
      ctx.beginPath()
      ctx.moveTo(mapX(s.x), mapY(s.y))
      ctx.lineTo(mapX(t.x), mapY(t.y))
      ctx.strokeStyle = '#aaa'
      ctx.lineWidth = 0.5
      ctx.stroke()
    })

    // 绘制节点（实心小圆点，使用节点自身的颜色）
    ctx.globalAlpha = 1
    simNodes.forEach((n) => {
      ctx.beginPath()
      ctx.arc(mapX(n.x ?? 0), mapY(n.y ?? 0), 2, 0, Math.PI * 2)
      ctx.fillStyle = n.color || '#999'
      ctx.fill()
    })
  }, [nodes, links])

  // 图谱数据变化时重新绘制小地图
  useEffect(() => {
    if (!showMinimap) return
    draw()
  }, [showMinimap, draw])

  // 持续动画循环：实时更新视口矩形框的位置和大小
  // 使用 ref 缓存 graphCanvas 以保持依赖稳定，避免每次重渲染时重启循环
  const graphCanvasRef = useRef(graphCanvas)
  useEffect(() => {
    graphCanvasRef.current = graphCanvas
  }, [graphCanvas])

  useEffect(() => {
    if (!showMinimap) return
    let rafId = null
    let lastX = 0, lastY = 0, lastK = 0

    const tick = () => {
      const gc = graphCanvasRef.current
      const t = gc?.transform?.current
      if (t) {
        // 仅在 transform 实际变化时更新视口位置，减少不必要的 DOM 操作
        if (t.x !== lastX || t.y !== lastY || t.k !== lastK) {
          lastX = t.x; lastY = t.y; lastK = t.k

          const vp = viewportRef.current
          if (vp) {
            // 计算所有节点的包围盒，用于视口坐标映射
            const simNodes = gc.nodes?.current || []
            if (simNodes.length) {
              let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity
              simNodes.forEach((n) => {
                if (n.x < minX) minX = n.x
                if (n.x > maxX) maxX = n.x
                if (n.y < minY) minY = n.y
                if (n.y > maxY) maxY = n.y
              })
              const pad = 40
              minX -= pad; minY -= pad; maxX += pad; maxY += pad
              const scaleX = MW / (maxX - minX || 1)
              const scaleY = MH / (maxY - minY || 1)
              const scale = Math.min(scaleX, scaleY)
              const ox = (MW - (maxX - minX) * scale) / 2
              const oy = (MH - (maxY - minY) * scale) / 2
              const mapX = (x) => ox + (x - minX) * scale
              const mapY = (y) => oy + (y - minY) * scale

              const W = window.innerWidth
              const H = window.innerHeight

              const vx0 = -t.x / t.k
              const vy0 = -t.y / t.k
              const vx1 = (W - t.x) / t.k
              const vy1 = (H - t.y) / t.k

              vp.style.left = mapX(vx0) + 'px'
              vp.style.top = mapY(vy0) + 'px'
              vp.style.width = Math.max(4, (vx1 - vx0) * scale) + 'px'
              vp.style.height = Math.max(4, (vy1 - vy0) * scale) + 'px'
            }
          }
        }
      }
      rafId = requestAnimationFrame(tick)
    }
    rafId = requestAnimationFrame(tick)
    return () => {
      if (rafId) cancelAnimationFrame(rafId)
    }
  }, [showMinimap])

  return (
    <div
      className={cn(
        'absolute bottom-3 left-3 z-10',
        'w-[180px] bg-card/90 backdrop-blur-sm',
        'border border-border rounded-xl',
        'shadow-md overflow-hidden',
        'transition-all duration-200',
        showMinimap
          ? 'opacity-100 pointer-events-auto'
          : 'opacity-0 pointer-events-none h-0 w-0 !border-0 !p-0'
      )}
    >
      <div className="relative" style={{ height: MH }}>
        <canvas
          ref={canvasRef}
          data-minimap
          className="w-full h-full block"
        />
        <div
          ref={viewportRef}
          data-minimap-viewport
          className="absolute border-2 border-primary rounded-sm pointer-events-none opacity-60"
        />
      </div>
    </div>
  )
}
