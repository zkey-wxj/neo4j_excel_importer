import { useState, useEffect, useRef } from 'react'
import { useAppStore } from '@/store'
import { cn } from '@/lib/utils'
import { Input } from '@/components/ui/input'

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

  return (
    <div
      className={cn(
        'absolute top-3 left-3 w-[200px] z-20',
        'bg-card border border-border rounded-xl',
        'px-3 py-2.5 shadow-md font-mono'
      )}
    >
      <Input
        placeholder="搜索 名称 / nid / 标签"
        value={value}
        onChange={handleChange}
        className="h-7 text-xs font-mono"
      />
    </div>
  )
}
