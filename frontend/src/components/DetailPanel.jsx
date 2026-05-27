import { useAppStore } from '@/store'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { ScrollArea } from '@/components/ui/scroll-area'

function clean(v) {
  return String(v || '').trim()
}

/**
 * 节点详情子组件
 * 展示选中节点的名称、类型标签、nid、属性、权重及关联节点列表
 */
function NodeDetail({ detailNode, nodes, links, onNavigate, onEdit, onClose }) {
  const raw = detailNode.raw || detailNode
  const nodeType = detailNode.type || 'Node'
  const nodeColor = detailNode.color || '#888'
  const nodeName = detailNode.label || clean(raw.name) || 'Unknown'
  const nodeNid = clean(raw.nid)
  const weight = detailNode.weight ?? 0

  // 查找当前节点的所有邻居（通过关系连接的节点）
  const nodeId = detailNode.id || nodeNid
  const neighbors = []
  ;(links || []).forEach((l) => {
    const srcId = typeof l.source === 'object' ? l.source?.id : l.source
    const tgtId = typeof l.target === 'object' ? l.target?.id : l.target
    const isSrc = srcId === nodeId
    const isTgt = tgtId === nodeId
    if (!isSrc && !isTgt) return
    const otherId = isSrc ? tgtId : srcId
    const otherNode = (nodes || []).find((n) => n.id === otherId)
    if (otherNode) {
      neighbors.push({
        node: otherNode,
        rel: clean(l.relation),
        dir: isSrc ? '->' : '<-',
      })
    }
  })

  const props = raw.properties || {}
  const propEntries = Object.entries(props).filter(
    ([, v]) => v != null && String(v).trim()
  )

  return (
    <>
      <div className="text-lg font-bold leading-tight mb-1" style={{ color: nodeColor }}>
        {nodeName}
      </div>
      <div className="flex flex-wrap gap-1 mb-3">
        {(Array.isArray(raw.labels) ? raw.labels : [nodeType]).map((label, i, arr) => (
          <Badge key={i} variant="outline"
            className={cn('text-[9.5px] tracking-widest font-semibold border-transparent', i === 0 && 'pl-0', i === arr.length - 1 && 'pr-0')}
            style={{ color: nodeColor }}>
            {clean(label)}
          </Badge>
        ))}
      </div>
      <div className="flex items-center gap-1.5 mb-4">
        <span className="text-[9px] text-muted-foreground uppercase tracking-widest">nid</span>
        <code className="text-[11px] font-semibold bg-muted/60 px-1.5 py-0.5 rounded break-all">{nodeNid}</code>
      </div>
      <div className="mb-3">
        <div className="text-[10px] text-muted-foreground tracking-wide mb-1.5 border-b border-border pb-1">属性 / 描述</div>
        {raw.description && (
          <div className="flex justify-between py-0.5 text-[11px]">
            <span className="text-muted-foreground">描述</span>
            <span className="font-semibold text-right max-w-[60%] break-all">{clean(raw.description).slice(0, 80)}</span>
          </div>
        )}
        {propEntries.map(([k, v]) => (
          <div key={k} className="flex justify-between py-0.5 text-[11px]">
            <span className="text-muted-foreground">{clean(k)}</span>
            <span className="font-semibold text-right max-w-[60%] break-all">{String(v).slice(0, 60)}</span>
          </div>
        ))}
        <div className="flex justify-between py-0.5 text-[11px]">
          <span className="text-muted-foreground">权重</span>
          <span className="font-semibold">{weight} / 100</span>
        </div>
      </div>
      <div className="mb-3">
        <div className="flex justify-between items-center border-b border-border pb-1 mb-1.5">
          <span className="text-[10px] text-muted-foreground tracking-wide">关联节点</span>
          <span className="text-[10px] text-muted-foreground">{neighbors.length}</span>
        </div>
        <div className="max-h-[200px] overflow-y-auto">
          {neighbors.length === 0 && <div className="text-[10px] text-muted-foreground/60 py-1">无关联节点</div>}
          {neighbors.slice(0, 30).map((nb, idx) => (
            <div key={idx} onClick={() => onNavigate(nb.node)}
              className="flex justify-between items-center py-1 px-1 rounded-md cursor-pointer text-[10.5px] hover:bg-muted/50 transition-colors">
              <span className="truncate max-w-[70%]">{nb.dir} {nb.node.label}</span>
              <span className="text-muted-foreground text-[9.5px] shrink-0 ml-2">{nb.rel}</span>
            </div>
          ))}
        </div>
      </div>
      <div className="flex gap-2 mt-2">
        <Button variant="outline" size="sm" className="flex-1 h-7 text-[11px] font-mono" onClick={onEdit}>编辑</Button>
      </div>
    </>
  )
}

/**
 * 关系详情子组件
 * 展示选中关系的类型、源节点、目标节点及属性信息
 */
function RelationDetail({ raw, nodes, onNavigate }) {
  const relType = clean(raw.rel_type) || 'RELATED'
  const srcNid = clean(raw.source_nid)
  const tgtNid = clean(raw.target_nid)
  const srcNode = (nodes || []).find((n) => n.id === srcNid || clean(n.nid) === srcNid)
  const tgtNode = (nodes || []).find((n) => n.id === tgtNid || clean(n.nid) === tgtNid)

  const props = raw.properties || {}
  const propEntries = Object.entries(props).filter(
    ([, v]) => v != null && String(v).trim()
  )

  return (
    <>
      <div className="text-lg font-bold leading-tight mb-3 text-primary">{relType}</div>
      <div className="mb-3">
        <div className="text-[10px] text-muted-foreground tracking-wide mb-1.5 border-b border-border pb-1">关系端点</div>
        <div className="flex justify-between items-center py-1 text-[11px]">
          <span className="text-muted-foreground">源节点</span>
          <span className={cn("font-semibold text-right max-w-[60%] break-all", srcNode && "cursor-pointer hover:underline")}
            onClick={() => srcNode && onNavigate(srcNode)}>
            {srcNode?.label || srcNid}
          </span>
        </div>
        <div className="flex justify-between items-center py-1 text-[11px]">
          <span className="text-muted-foreground">目标节点</span>
          <span className={cn("font-semibold text-right max-w-[60%] break-all", tgtNode && "cursor-pointer hover:underline")}
            onClick={() => tgtNode && onNavigate(tgtNode)}>
            {tgtNode?.label || tgtNid}
          </span>
        </div>
      </div>
      {(raw.description || propEntries.length > 0) && (
        <div className="mb-3">
          <div className="text-[10px] text-muted-foreground tracking-wide mb-1.5 border-b border-border pb-1">属性 / 描述</div>
          {raw.description && (
            <div className="flex justify-between py-0.5 text-[11px]">
              <span className="text-muted-foreground">描述</span>
              <span className="font-semibold text-right max-w-[60%] break-all">{clean(raw.description).slice(0, 80)}</span>
            </div>
          )}
          {propEntries.map(([k, v]) => (
            <div key={k} className="flex justify-between py-0.5 text-[11px]">
              <span className="text-muted-foreground">{clean(k)}</span>
              <span className="font-semibold text-right max-w-[60%] break-all">{String(v).slice(0, 60)}</span>
            </div>
          ))}
        </div>
      )}
    </>
  )
}

/**
 * 详情面板组件
 * 在右侧滑出显示选中节点或关系的详细信息面板，
 * 支持节点/关系两种视图切换，以及节点间导航和编辑操作
 */
export default function DetailPanel() {
  const detailNode = useAppStore((s) => s.detailNode)
  const setDetailNode = useAppStore((s) => s.setDetailNode)
  const nodes = useAppStore((s) => s.nodes)
  const links = useAppStore((s) => s.links)
  const setSelectedNode = useAppStore((s) => s.setSelectedNode)
  const setHoveredNode = useAppStore((s) => s.setHoveredNode)
  const setOpsExpand = useAppStore((s) => s.setOpsExpand)

  if (!detailNode) return null

  const isRelation = detailNode._type === 'relation'

  const handleClose = () => {
    setDetailNode(null)
    setSelectedNode(null)
    setHoveredNode(null)
  }

  const handleNavigate = (neighborNode) => {
    setDetailNode(neighborNode)
    setSelectedNode(neighborNode.id)
  }

  const handleEdit = () => {
    setSelectedNode(detailNode.id)
    setOpsExpand(true)
  }

  return (
    <div className={cn(
      'absolute top-3 right-3 w-[280px] max-h-[calc(100dvh-32px)] z-20',
      'bg-card border border-border rounded-2xl',
      'shadow-lg p-5 font-mono',
      'animate-in slide-in-from-right-3 fade-in-0 duration-200'
    )}>
      <ScrollArea className="max-h-[calc(100dvh-80px)]">
        <button onClick={handleClose}
          className="absolute top-1 right-1 text-muted-foreground hover:text-foreground p-1 rounded-md hover:bg-muted/50 transition-colors text-lg leading-none">
          x
        </button>
        {isRelation ? (
          <RelationDetail raw={detailNode.raw} nodes={nodes} onNavigate={handleNavigate} />
        ) : (
          <NodeDetail detailNode={detailNode} nodes={nodes} links={links}
            onNavigate={handleNavigate} onEdit={handleEdit} onClose={handleClose} />
        )}
      </ScrollArea>
    </div>
  )
}
