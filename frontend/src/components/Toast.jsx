import { Toaster as SonnerToaster } from 'sonner'

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
