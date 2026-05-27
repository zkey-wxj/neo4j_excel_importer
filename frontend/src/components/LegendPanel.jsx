import { useMemo, useState } from 'react'
import { useAppStore } from '@/store'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { Collapsible, CollapsibleTrigger, CollapsibleContent } from '@/components/ui/collapsible'
import { Palette, ChevronRight } from 'lucide-react'

function clean(v) { return String(v || '').trim() }

const REL_COLORS = [
  '#8ecae6', '#219ebc', '#fb8500', '#ffb703', '#023047',
  '#d62828', '#f77f00', '#fcbf49', '#6a994e', '#bc6c25',
  '#e76f51', '#264653', '#2a9d8f', '#e9c46a', '#606c38',
]

/**
 * 图例项组件
 * 显示单个类型的颜色标识和名称，支持点击切换筛选
 * @param {boolean} isRel - 是否为关系类型（关系显示为横线，节点显示为圆点）
 */
function LegendItem({ label, color, active, onClick, isRel }) {
  return (
    <div onClick={onClick} className={cn(
      'flex items-center gap-2 py-0.5 px-1.5 rounded-md cursor-pointer select-none transition-all text-[11px] font-mono',
      'border border-transparent',
      active ? 'bg-primary/10 border-primary/25' : 'hover:bg-muted/40 hover:border-border'
    )}>
      {isRel
        ? <div className="w-4 h-[3px] rounded-sm shrink-0" style={{ background: color }} />
        : <div className="w-2.5 h-2.5 rounded-full shrink-0" style={{ background: color }} />}
      <span className="truncate">{label}</span>
    </div>
  )
}

/**
 * 图例面板组件
 * 右下角的可折叠图例，支持按节点类型和关系类型筛选图谱
 * 折叠状态仅显示调色板图标，展开后显示类型列表和重置筛选按钮
 * 关系类型使用预定义的 15 种循环颜色方案
 */
export default function LegendPanel() {
  const [collapsed, setCollapsed] = useState(true)
  const [activeTab, setActiveTab] = useState('node')
  const nodes = useAppStore((s) => s.nodes)
  const links = useAppStore((s) => s.links)
  const selectedLegendTypes = useAppStore((s) => s.selectedLegendTypes)
  const toggleLegendType = useAppStore((s) => s.toggleLegendType)
  const clearLegendTypes = useAppStore((s) => s.clearLegendTypes)
  const selectedRelTypes = useAppStore((s) => s.selectedRelTypes)
  const toggleRelType = useAppStore((s) => s.toggleRelType)
  const clearRelTypes = useAppStore((s) => s.clearRelTypes)

  // 从节点数据中提取去重的节点类型列表（含颜色信息）
  const nodeTypes = useMemo(() => {
    const byType = new Map()
    ;(nodes || []).forEach((n) => {
      const labels = Array.isArray(n.raw?.labels) ? n.raw.labels.filter(Boolean) : [n.type || 'Node']
      labels.forEach((l) => {
        const key = clean(l).toLowerCase()
        if (!key || byType.has(key)) return
        byType.set(key, { key, display: clean(l), color: n.color || '#888' })
      })
    })
    return [...byType.values()]
  }, [nodes])

  // 从关系数据中提取去重的关系类型列表
  const relTypes = useMemo(() => {
    const byType = new Map()
    ;(links || []).forEach((l) => {
      const rt = clean(l.relation)
      const key = rt.toLowerCase()
      if (!key || byType.has(key)) return
      byType.set(key, { key, display: rt })
    })
    return [...byType.values()]
  }, [links])

  if (collapsed) {
    return (
      <div className="absolute bottom-3 right-3 z-10">
        <Button variant="outline" size="icon" onClick={() => setCollapsed(false)}
          className="size-[34px] bg-card/90 backdrop-blur-md border-border shadow-md rounded-lg text-muted-foreground hover:text-foreground">
          <Palette className="size-3.5" />
        </Button>
      </div>
    )
  }

  const isNode = activeTab === 'node'
  const activeSet = isNode ? selectedLegendTypes : selectedRelTypes
  const items = isNode ? nodeTypes : relTypes
  const toggle = isNode ? toggleLegendType : toggleRelType
  const clear = isNode ? clearLegendTypes : clearRelTypes

  return (
    <div className="absolute bottom-3 right-3 z-10 w-[200px]">
      <div className="bg-card/95 backdrop-blur-md border border-border rounded-xl shadow-lg font-mono flex flex-col">
        {/* Tab header */}
        <div className="flex items-center px-3 pt-2.5 pb-1.5 gap-2">
          <button onClick={() => setActiveTab('node')}
            className={cn('text-[10px] px-2 py-0.5 rounded-md transition-colors',
              isNode ? 'bg-primary/10 text-primary' : 'text-muted-foreground hover:text-foreground')}>
            节点类型
          </button>
          <button onClick={() => setActiveTab('rel')}
            className={cn('text-[10px] px-2 py-0.5 rounded-md transition-colors',
              !isNode ? 'bg-primary/10 text-primary' : 'text-muted-foreground hover:text-foreground')}>
            关系类型
          </button>
          <div className="flex-1" />
          <Button variant="ghost" size="icon" onClick={() => setCollapsed(true)}
            className="size-5 text-muted-foreground hover:text-foreground">
            <ChevronRight className="size-3" />
          </Button>
        </div>

        {/* Content */}
        <div className="max-h-[200px] overflow-y-auto graph-scrollbar px-3 pb-2">
          {items.length === 0 && (
            <div className="text-[10px] text-muted-foreground/60 py-1">
              {isNode ? '暂无节点' : '暂无关系'}
            </div>
          )}
          {items.map((item, idx) => (
            <LegendItem
              key={item.key}
              label={item.display}
              color={isNode ? item.color : REL_COLORS[idx % REL_COLORS.length]}
              active={activeSet.has(item.key)}
              onClick={() => toggle(item.key)}
              isRel={!isNode}
            />
          ))}
        </div>

        {/* Reset button */}
        {activeSet.size > 0 && (
          <div className="px-3 pb-2.5 pt-1 border-t border-border">
            <Button variant="outline" size="sm" onClick={clear}
              className="w-full h-6 text-[10px] font-mono">
              重置筛选
            </Button>
          </div>
        )}
      </div>
    </div>
  )
}
