import { useAppStore } from '@/store'
import { cn } from '@/lib/utils'

/**
 * 全屏加载遮罩组件
 * 在图谱数据初始加载或切换分组时显示，包含旋转加载动画和提示文字
 * 通过 opacity 和 pointer-events 实现平滑的显示/隐藏过渡
 */
export default function FullscreenLoading() {
  const isFullscreenLoading = useAppStore((s) => s.isFullscreenLoading)

  return (
    <div
      className={cn(
        'fixed inset-0 z-[200] flex flex-col items-center justify-center gap-5',
        'bg-background/90 backdrop-blur-sm',
        'transition-opacity duration-300',
        isFullscreenLoading
          ? 'opacity-100 pointer-events-auto'
          : 'opacity-0 pointer-events-none'
      )}
    >
      <div className="w-9 h-9 rounded-full border-[3px] border-muted border-t-primary animate-spin" />
      <span className="font-mono text-xs text-muted-foreground tracking-widest">
        图谱加载中...
      </span>
    </div>
  )
}
