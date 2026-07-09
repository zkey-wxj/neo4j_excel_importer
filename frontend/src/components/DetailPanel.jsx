import { useAppStore } from '@/store'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Trash2 } from 'lucide-react'
import { toast } from 'sonner'

function clean(v) {
  return String(v || '').trim()
}

/**
 * 节点详情子组件
 * 展示选中节点的名称、类型标签、nid、属性、权重及关联节点列表
 */
function NodeDetail({ detailNode, nodes, links, onNavigate, onEdit }) {
  const raw = detailNode.raw || detailNode
  const nodeType = detailNode.type || 'Node'
  const nodeColor = detailNode.color || '#888'
  const nodeName = detailNode.label || clean(raw.name) || 'Unknown'
  const nodeNid = clean(raw.nid)
  const weight = detailNode.weight ?? 0

  // 删除关系所需的 store 方法
  const mutate = useAppStore((s) => s.mutate)
  const confirm = useAppStore((s) => s.confirm)

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
        link: l,
      })
    }
  })

  // 删除该邻居关系：二次确认后调用后端 DELETE，成功后前端 store 同步移除
  const handleDeleteRel = async (e, nb) => {
    e.stopPropagation()
    const relRaw = nb.link?.raw || {}
    const groupId = clean(relRaw.group_id) || clean(raw.group_id)
    const sourceNid = clean(relRaw.source_nid)
    const targetNid = clean(relRaw.target_nid)
    const relType = clean(relRaw.rel_type)
    if (!groupId || !sourceNid || !targetNid) {
      toast.error('关系信息不完整，无法删除')
      return
    }
    // 按 nid 匹配两端 label：邻居为另一端，当前节点为本端
    const nbLabel = nb.node?.label || clean(nb.node?.nid) || '-'
    const srcLabel = sourceNid === nodeNid ? nodeName : nbLabel
    const tgtLabel = targetNid === nodeNid ? nodeName : nbLabel
    const ok = await confirm(
      `确认删除关系？\n源节点: ${srcLabel} (${sourceNid})\n目标节点: ${tgtLabel} (${targetNid})\n类型: ${relType || '-'}`
    )
    if (!ok) return
    const r = await mutate('/group-graph/api/relation', 'DELETE', {
      group_id: groupId,
      source_nid: sourceNid,
      target_nid: targetNid,
      rel_type: relType,
    })
    r ? toast.success('删除成功') : toast.error('删除失败')
  }

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
            className={cn('text-xs tracking-widest font-semibold border-transparent', i === 0 && 'pl-0', i === arr.length - 1 && 'pr-0')}
            style={{ color: nodeColor }}>
            {clean(label)}
          </Badge>
        ))}
      </div>
      <div className="flex items-center gap-1.5 mb-4">
        <span className="text-xs text-muted-foreground uppercase tracking-widest">nid</span>
        <code className="text-sm font-semibold bg-muted/60 px-1.5 py-0.5 rounded break-all">{nodeNid}</code>
      </div>
      <div className="mb-3">
        <div className="text-xs text-muted-foreground tracking-wide mb-1.5 border-b border-border pb-1">属性 / 描述</div>
        {raw.description && (
          <div className="flex justify-between py-0.5 text-xs">
            <span className="text-muted-foreground">描述</span>
            <span className="font-semibold text-right max-w-[60%] break-all">{clean(raw.description).slice(0, 80)}</span>
          </div>
        )}
        {propEntries.map(([k, v]) => (
          <div key={k} className="flex justify-between py-0.5 text-xs">
            <span className="text-muted-foreground">{clean(k)}</span>
            <span className="font-semibold text-right max-w-[60%] break-all">{String(v).slice(0, 60)}</span>
          </div>
        ))}
        <div className="flex justify-between py-0.5 text-xs">
          <span className="text-muted-foreground">权重</span>
          <span className="font-semibold">{weight} / 100</span>
        </div>
      </div>
      <div className="mb-3">
        <div className="flex justify-between items-center border-b border-border pb-1 mb-1.5">
          <span className="text-xs text-muted-foreground tracking-wide">关联节点</span>
          <span className="text-xs text-muted-foreground">{neighbors.length}</span>
        </div>
        <div className="max-h-[200px] overflow-y-auto">
          {neighbors.length === 0 && <div className="text-xs text-muted-foreground/60 py-1">无关联节点</div>}
          {neighbors.slice(0, 30).map((nb, idx) => (
            <div key={idx} onClick={() => onNavigate(nb.node)}
              className="flex justify-between items-center py-1 px-1 rounded-md cursor-pointer text-xs hover:bg-muted/50 transition-colors">
              <span className="truncate max-w-[55%]">{nb.dir} {nb.node.label}</span>
              <div className="flex items-center gap-1.5 shrink-0 ml-2">
                <span className="text-muted-foreground text-xs">{nb.rel}</span>
                <button type="button" title="删除关系"
                  onClick={(e) => handleDeleteRel(e, nb)}
                  className="text-muted-foreground/60 hover:text-destructive transition-colors leading-none">
                  <Trash2 className="size-3" />
                </button>
              </div>
            </div>
          ))}
        </div>
      </div>
      <div className="flex gap-2 mt-2">
        <Button variant="outline" size="sm" className="flex-1 h-7 text-xs font-mono" onClick={onEdit}>编辑</Button>
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
        <div className="text-xs text-muted-foreground tracking-wide mb-1.5 border-b border-border pb-1">关系端点</div>
        <div className="flex justify-between items-center py-1 text-xs">
          <span className="text-muted-foreground">源节点</span>
          <span className={cn("font-semibold text-right max-w-[60%] break-all", srcNode && "cursor-pointer hover:underline")}
            onClick={() => srcNode && onNavigate(srcNode)}>
            {srcNode?.label || srcNid}
          </span>
        </div>
        <div className="flex justify-between items-center py-1 text-xs">
          <span className="text-muted-foreground">目标节点</span>
          <span className={cn("font-semibold text-right max-w-[60%] break-all", tgtNode && "cursor-pointer hover:underline")}
            onClick={() => tgtNode && onNavigate(tgtNode)}>
            {tgtNode?.label || tgtNid}
          </span>
        </div>
      </div>
      {(raw.description || propEntries.length > 0) && (
        <div className="mb-3">
          <div className="text-xs text-muted-foreground tracking-wide mb-1.5 border-b border-border pb-1">属性 / 描述</div>
          {raw.description && (
            <div className="flex justify-between py-0.5 text-xs">
              <span className="text-muted-foreground">描述</span>
              <span className="font-semibold text-right max-w-[60%] break-all">{clean(raw.description).slice(0, 80)}</span>
            </div>
          )}
          {propEntries.map(([k, v]) => (
            <div key={k} className="flex justify-between py-0.5 text-xs">
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
