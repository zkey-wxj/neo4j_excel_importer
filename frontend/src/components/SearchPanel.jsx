import { useState, useEffect, useRef } from 'react'
import { useAppStore } from '@/store'
import { cn } from '@/lib/utils'
import { Input } from '@/components/ui/input'
import { X } from 'lucide-react'

/**
 * 搜索面板组件
 * 位于左上角的搜索输入框，支持按节点名称、nid、标签进行模糊搜索
 * 使用防抖（300ms）策略减少频繁搜索触发
 */
export default function SearchPanel() {
  const setSearchKeyword = useAppStore((s) => s.setSearchKeyword)
  const [value, setValue] = useState('')
  const timerRef = useRef(null)

  useEffect(() => {
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current)
    }
  }, [])

  // 输入变化处理：更新本地值，防抖 300ms 后同步关键词到全局 store
  const handleChange = (e) => {
    const v = e.target.value
    setValue(v)
    if (timerRef.current) clearTimeout(timerRef.current)
    timerRef.current = setTimeout(() => {
      setSearchKeyword(v.trim().toLowerCase())
    }, 300)
  }

  /** 清空搜索：重置输入值和全局关键词 */
  const handleClear = () => {
    setValue('')
    if (timerRef.current) clearTimeout(timerRef.current)
    setSearchKeyword('')
  }

  return (
    <div
      className={cn(
        'absolute top-3 left-3 w-[200px] z-20',
        'bg-card border border-border rounded-xl',
        'shadow-md font-mono'
      )}
    >
      <div className="relative">
        <Input
          placeholder="搜索 名称 / nid / 标签"
          value={value}
          onChange={handleChange}
          className={cn("h-7 text-xs font-mono", value && "pr-7")}
        />
        {value && (
          <button
            onClick={handleClear}
            className="absolute right-1.5 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground transition-colors"
          >
            <X className="size-3.5" />
          </button>
        )}
      </div>
    </div>
  )
}
