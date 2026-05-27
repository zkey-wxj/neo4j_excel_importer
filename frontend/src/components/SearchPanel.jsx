import { useState, useEffect, useRef } from 'react'
import { useAppStore } from '@/store'
import { cn } from '@/lib/utils'
import { Input } from '@/components/ui/input'
import { X } from 'lucide-react'

export default function SearchPanel() {
  const setSearchKeyword = useAppStore((s) => s.setSearchKeyword)
  const [value, setValue] = useState('')
  const timerRef = useRef(null)

  useEffect(() => {
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current)
    }
  }, [])

  const handleChange = (e) => {
    const v = e.target.value
    setValue(v)
    if (timerRef.current) clearTimeout(timerRef.current)
    timerRef.current = setTimeout(() => {
      setSearchKeyword(v.trim().toLowerCase())
    }, 300)
  }

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
