import { useRef } from 'react'
import * as d3 from 'd3'
import { useAppStore } from '@/store'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '@/components/ui/tooltip'

/**
 * 控制栏按钮组件：带工具提示的图标按钮
 * @param {string} title - 工具提示文本
 * @param {function} onClick - 点击回调
 * @param {boolean} active - 是否为激活状态（高亮显示）
 * @param {React.ReactNode} children - 按钮图标内容
 */
function CtrlBtn({ title, onClick, active, children }) {
  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <Button
            variant="outline"
            size="icon"
            className={cn(
              'w-[34px] h-[34px] rounded-lg border-border bg-card text-muted-foreground',
              'hover:bg-primary/10 hover:border-primary/30 hover:text-primary',
              'active:scale-95 transition-all shadow-sm',
              active && 'border-primary/40 text-primary bg-primary/10'
            )}
            onClick={onClick}
          />
        }
      >
        {children}
      </TooltipTrigger>
      <TooltipContent side="top" className="text-[10px] font-mono">
        {title}
      </TooltipContent>
    </Tooltip>
  )
}

/**
 * 底部控制栏组件
 * 提供图谱画布的操作按钮：缩放（放大/缩小/重置）、小地图开关、
 * 数据导出（JSON/PNG/Excel）和数据导入功能
 */
export default function ControlBar({ graphCanvas }) {
  const setShowImportModal = useAppStore((s) => s.setShowImportModal)
  const setImportFile = useAppStore((s) => s.setImportFile)
  const groupId = useAppStore((s) => s.groupId)
  const showMinimap = useAppStore((s) => s.showMinimap)
  const setShowMinimap = useAppStore((s) => s.setShowMinimap)
  const zoom = useAppStore((s) => s.zoom)
  const setZoom = useAppStore((s) => s.setZoom)
  const nodes = useAppStore((s) => s.nodes)
  const links = useAppStore((s) => s.links)
  const setStatus = useAppStore((s) => s.setStatus)
  const fileInputRef = useRef(null)

  /** 放大画布：缩放比例增加 35%，最大 4 倍 */
  const handleZoomIn = () => {
    const newK = Math.min(4, (zoom ?? 1) * 1.35)
    graphCanvas?.zoomTo?.(newK)
  }

  /** 缩小画布：缩放比例减少 26%，最小 0.1 倍 */
  const handleZoomOut = () => {
    const newK = Math.max(0.1, (zoom ?? 1) * 0.74)
    graphCanvas?.zoomTo?.(newK)
  }

  /** 重置视图：将画布变换重置为初始状态（缩放 1x，无平移） */
  const handleReset = () => {
    if (graphCanvas?.transform) {
      graphCanvas.transform.current = d3.zoomIdentity
    }
    setZoom(1)
    graphCanvas?.scheduleRender?.()
  }

  /** 导出图谱数据为 JSON 文件，包含节点和关系的原始数据 */
  const handleExportJSON = () => {
    const data = {
      nodes: (nodes || []).map((n) => n.raw || n),
      relations: (links || []).map((l) => l.raw || l),
    }
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = `graph_${groupId || 'export'}.json`
    a.click()
    setStatus('JSON 已导出')
  }

  /** 将当前画布内容导出为 PNG 图片 */
  const handleExportPNG = () => {
    const canvas = document.querySelector('canvas')
    if (!canvas) return
    canvas.toBlob((blob) => {
      if (!blob) return
      const a = document.createElement('a')
      a.href = URL.createObjectURL(blob)
      a.download = `graph_${groupId || 'export'}.png`
      a.click()
      setStatus('PNG 已导出')
    })
  }

  /**
   * 导出图谱数据为 Excel 文件
   * 生成两个工作表：「节点」和「关系」，自动收集动态属性列
   */
  const handleExportExcel = async () => {
    const rawNodes = (nodes || []).map((n) => n.raw || n)
    const rawLinks = (links || []).map((l) => l.raw || l)
    if (!rawNodes.length && !rawLinks.length) {
      const { toast } = await import('sonner')
      toast.error('没有可导出的数据')
      return
    }

    const XLSX = await import('xlsx')

    // 收集节点的动态属性字段作为额外表头
    const nodeExtra = new Set()
    rawNodes.forEach((n) =>
      Object.keys(n.properties || {}).forEach((k) => nodeExtra.add(k))
    )
    const nodeHeaders = ['nid', 'name', 'labels', 'description', ...[...nodeExtra].sort()]
    const nodeRows = rawNodes.map((n) => {
      const row = [n.nid || '', n.name || '', (n.labels || []).join(', '), n.description || '']
      nodeExtra.forEach((k) => row.push((n.properties || {})[k] ?? ''))
      return row
    })

    // 收集关系的动态属性字段作为额外表头
    const relExtra = new Set()
    rawLinks.forEach((r) =>
      Object.keys(r.properties || {}).forEach((k) => relExtra.add(k))
    )
    const relHeaders = ['source_nid', 'rel_type', 'target_nid', 'description', ...[...relExtra].sort()]
    const relRows = rawLinks.map((r) => {
      const row = [r.source_nid || '', r.rel_type || '', r.target_nid || '', r.description || '']
      relExtra.forEach((k) => row.push((r.properties || {})[k] ?? ''))
      return row
    })

    const wb = XLSX.utils.book_new()
    const wsNode = XLSX.utils.aoa_to_sheet([nodeHeaders, ...nodeRows])
    XLSX.utils.book_append_sheet(wb, wsNode, '节点')
    const wsRel = XLSX.utils.aoa_to_sheet([relHeaders, ...relRows])
    XLSX.utils.book_append_sheet(wb, wsRel, '关系')
    XLSX.writeFile(wb, `graph_${groupId || 'graph'}.xlsx`)
    setStatus('Excel 导出完成')
  }

  /** 点击导入按钮：校验 group_id 后触发文件选择对话框 */
  const handleImportClick = () => {
    if (!groupId) {
      import('sonner').then(({ toast }) => toast.error('请先输入 group_id'))
      return
    }
    fileInputRef.current?.click()
  }

  /** 文件选择完成：将选中文件存入 store 并打开导入确认弹窗 */
  const handleFileChange = (e) => {
    const file = e.target.files?.[0]
    if (!file) return
    setImportFile(file)
    setShowImportModal(true)
    e.target.value = ''
  }

  return (
      <div
        className={cn(
          'absolute bottom-3 left-1/2 -translate-x-1/2 z-10',
          'flex gap-1.5'
        )}
      >
        <CtrlBtn title="放大" onClick={handleZoomIn}>
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round">
            <circle cx="7" cy="7" r="5" />
            <line x1="11" y1="11" x2="14" y2="14" />
            <line x1="5" y1="7" x2="9" y2="7" />
            <line x1="7" y1="5" x2="7" y2="9" />
          </svg>
        </CtrlBtn>
        <CtrlBtn title="缩小" onClick={handleZoomOut}>
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round">
            <circle cx="7" cy="7" r="5" />
            <line x1="11" y1="11" x2="14" y2="14" />
            <line x1="5" y1="7" x2="9" y2="7" />
          </svg>
        </CtrlBtn>
        <CtrlBtn title="重置视图" onClick={handleReset}>
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
            <path d="M2 8a6 6 0 0 1 10.3-4.2" />
            <path d="M14 8a6 6 0 0 1-10.3 4.2" />
            <polyline points="2 2 2 5 5 5" />
            <polyline points="14 14 14 11 11 11" />
          </svg>
        </CtrlBtn>
        <CtrlBtn title="小地图" active={showMinimap} onClick={() => setShowMinimap(!showMinimap)}>
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round">
            <circle cx="8" cy="8" r="6" />
            <ellipse cx="8" cy="8" rx="3" ry="6" />
            <line x1="2" y1="6" x2="14" y2="6" />
            <line x1="2" y1="10" x2="14" y2="10" />
          </svg>
        </CtrlBtn>
        <CtrlBtn title="导出 JSON" onClick={handleExportJSON}>
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M4 2C3 2 2 3 2 4v2c0 1-1 1-1 2s1 1 1 2v2c0 1 1 2 2 2" />
            <path d="M12 2c1 0 2 1 2 2v2c0 1 1 1 1 2s-1 1-1 2v2c0 1-1 2-2 2" />
          </svg>
        </CtrlBtn>
        <CtrlBtn title="导出 PNG" onClick={handleExportPNG}>
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
            <rect x="2" y="3" width="12" height="10" rx="1.5" />
            <circle cx="5.5" cy="6.5" r="1.5" />
            <path d="M14 10l-3-3-7 7" />
          </svg>
        </CtrlBtn>
        <CtrlBtn title="导出 Excel" onClick={handleExportExcel}>
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <rect x="2" y="2" width="12" height="12" rx="2" />
            <line x1="2" y1="6" x2="14" y2="6" />
            <line x1="2" y1="10" x2="14" y2="10" />
            <line x1="6" y1="2" x2="6" y2="14" />
            <line x1="10" y1="2" x2="10" y2="14" />
          </svg>
        </CtrlBtn>
        <CtrlBtn title="导入 Excel/JSON" onClick={handleImportClick}>
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M8 10V2" />
            <path d="M5 5l3-3 3 3" />
            <path d="M2 11v2c0 1 1 2 2 2h8c1 0 2-1 2-2v-2" />
          </svg>
        </CtrlBtn>
        <input
          ref={fileInputRef}
          type="file"
          accept=".xlsx,.xls,.json"
          className="hidden"
          onChange={handleFileChange}
        />
      </div>
  )
}
