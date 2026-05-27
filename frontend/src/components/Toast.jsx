import { Toaster as SonnerToaster } from 'sonner'

/**
 * 消息通知组件（Toast）
 * 使用 sonner 库实现顶部居中的操作结果提示
 * 已在 App.jsx 中直接内联使用，此文件为备选独立引用方式
 */
export default function Toast() {
  return (
    <SonnerToaster
      position="top-center"
      richColors
      closeButton
      toastOptions={{
        className: 'font-mono text-xs',
        style: {
          background: 'var(--popover)',
          color: 'var(--popover-foreground)',
          border: '1px solid var(--border)',
          borderRadius: 'var(--radius)',
        },
      }}
    />
  )
}
