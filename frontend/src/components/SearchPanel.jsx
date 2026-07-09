import { useState, useEffect, useRef, useMemo } from 'react'
import { useAppStore } from '@/store'
import { cn } from '@/lib/utils'
import { Input } from '@/components/ui/input'
import { Search, X } from 'lucide-react'

/**
 * 搜索面板组件
 * 位于左上角的搜索输入框，点击「搜索」按钮后在输入框下方弹出节点列表面板。
 * 面板列出当前图谱全部节点（输入关键词实时过滤），点击节点项会在图谱上
 * 自动定位并选中该节点；面板保持展开，不会因选择而消失。
 */
export default function SearchPanel({ graphCanvas }) {
  const nodes = useAppStore((s) => s.nodes)
  const selectedNodeId = useAppStore((s) => s.selectedNodeId)
  const setSelectedNode = useAppStore((s) => s.setSelectedNode)
  const setHoveredNode = useAppStore((s) => s.setHoveredNode)
  const setDetailNode = useAppStore((s) => s.setDetailNode)

  const [query, setQuery] = useState('')
  const [open, setOpen] = useState(false)
  const [activeIndex, setActiveIndex] = useState(-1)
  const containerRef = useRef(null)
  const listRef = useRef(null)
  const itemRefs = useRef([])

  // 节点扁平列表：按标签 + 名称排序，便于滚动浏览
  const nodeList = useMemo(() => {
    return [...nodes].sort((a, b) => {
      const la = (a.type || a.labels?.[0] || '').toLowerCase()
      const lb = (b.type || b.labels?.[0] || '').toLowerCase()
      if (la !== lb) return la.localeCompare(lb)
      const na = (a.label || a.name || '').toLowerCase()
      const nb = (b.label || b.name || '').toLowerCase()
      return na.localeCompare(nb)
    })
  }, [nodes])

  // 实时过滤：按标签 / 名称 / nid / id 模糊匹配
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return nodeList
    return nodeList.filter((n) => {
      const hay = [
        n.type || '',
        n.label || '',
        n.name || '',
        n.nid || '',
        n.id || '',
        ...(Array.isArray(n.labels) ? n.labels : []),
      ]
        .join('|')
        .toLowerCase()
      return hay.includes(q)
    })
  }, [nodeList, query])

  // 点击面板外部：关闭面板（保留输入框）
  useEffect(() => {
    if (!open) return
    const handler = (e) => {
      if (containerRef.current && !containerRef.current.contains(e.target)) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open])

  // 滚动到激活项（键盘上下键时使用）
  useEffect(() => {
    if (activeIndex < 0) return
    itemRefs.current[activeIndex]?.scrollIntoView({ block: 'nearest' })
  }, [activeIndex])

  /** 执行搜索：打开面板并聚焦第一项 */
  const handleSearch = () => {
    setOpen(true)
    setActiveIndex(filtered.length ? 0 : -1)
  }

  /** 清空输入并关闭面板 */
  const handleClear = () => {
    setQuery('')
    setOpen(false)
    setActiveIndex(-1)
  }

  /** 选中节点：定位到图谱 + 选中 + 详情面板，保持结果面板不消失 */
  const handleSelect = (n) => {
    setSelectedNode(n.id)
    setHoveredNode(n.id)
    setDetailNode(n)
    graphCanvas?.focusNode?.(n.id)
  }

  /** 键盘导航：上下移动激活项，回车选中，Esc 关闭 */
  const handleKeyDown = (e) => {
    if (!open) {
      if (e.key === 'Enter') {
        e.preventDefault()
        handleSearch()
      }
      return
    }
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setActiveIndex((i) => (i + 1) % filtered.length)
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setActiveIndex((i) => (i - 1 + filtered.length) % filtered.length)
    } else if (e.key === 'Enter') {
      e.preventDefault()
      if (activeIndex >= 0 && filtered[activeIndex]) {
        handleSelect(filtered[activeIndex])
      }
    } else if (e.key === 'Escape') {
      e.preventDefault()
      setOpen(false)
    }
  }

  return (
    <div
      ref={containerRef}
      className={cn(
        'absolute top-3 left-3 w-[260px] z-30',
        'bg-card border border-border rounded-xl',
        'shadow-md font-mono'
      )}
    >
      {/* 搜索行：输入框 + 搜索按钮 + 清空按钮 */}
      <div className="flex items-center gap-1 p-1.5">
        <div className="relative flex-1">
          <Input
            placeholder="搜索 名称 / nid / 标签"
            value={query}
            onChange={(e) => {
              setQuery(e.target.value)
              if (!open) setOpen(true)
            }}
            onKeyDown={handleKeyDown}
            className={cn('h-7 text-xs font-mono', query && 'pr-6')}
          />
          {query && (
            <button
              onClick={handleClear}
              className="absolute right-1.5 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground transition-colors"
              title="清空"
            >
              <X className="size-3.5" />
            </button>
          )}
        </div>
        <button
          onClick={handleSearch}
          className={cn(
            'h-7 px-2 shrink-0 inline-flex items-center gap-1 rounded-md text-xs',
            'bg-primary text-primary-foreground hover:bg-primary/90 transition-colors'
          )}
          title="搜索节点"
        >
          <Search className="size-3.5" />
          搜索
        </button>
      </div>

      {/* 结果面板：全部节点列表，最大高度 + 滚动条 */}
      {open && (
        <div className="border-t border-border">
          <div className="px-2 py-1 text-[10px] text-muted-foreground flex items-center justify-between">
            <span>
              {query.trim() ? `过滤结果 ${filtered.length} / ${nodeList.length}` : `全部节点 ${nodeList.length}`}
            </span>
            <button
              onClick={() => setOpen(false)}
              className="hover:text-foreground transition-colors"
              title="关闭"
            >
              <X className="size-3" />
            </button>
          </div>
          <ul
            ref={listRef}
            className="overflow-y-auto overflow-x-hidden divide-y divide-border/60"
            style={{ maxHeight: 'min(60dvh, 420px)' }}
          >
            {filtered.length === 0 && (
              <li className="px-3 py-4 text-xs text-muted-foreground text-center">
                无匹配节点
              </li>
            )}
            {filtered.map((n, i) => {
              const isSelected = n.id === selectedNodeId
              const isActive = i === activeIndex
              const typeLabel = n.type || n.labels?.[0] || 'Node'
              return (
                <li key={n.id}>
                  <button
                    ref={(el) => (itemRefs.current[i] = el)}
                    onClick={() => handleSelect(n)}
                    onMouseEnter={() => setActiveIndex(i)}
                    className={cn(
                      'w-full text-left px-2 py-1.5 flex items-center gap-2 transition-colors',
                      isSelected && 'bg-primary/10',
                      isActive && !isSelected && 'bg-muted/60'
                    )}
                  >
                    <span
                      className="size-2.5 rounded-full shrink-0 ring-1 ring-border"
                      style={{ backgroundColor: n.color || '#94a3b8' }}
                    />
                    <span className="flex-1 min-w-0">
                      <span className="block text-xs text-foreground truncate">
                        {n.label || n.name || n.id}
                      </span>
                      <span className="block text-[10px] text-muted-foreground truncate">
                        {typeLabel}
                        {n.nid ? ` · ${n.nid}` : n.id !== n.label ? ` · ${n.id}` : ''}
                      </span>
                    </span>
                    {isSelected && (
                      <span className="text-[10px] text-primary shrink-0">已选</span>
                    )}
                  </button>
                </li>
              )
            })}
          </ul>
        </div>
      )}
    </div>
  )
}
