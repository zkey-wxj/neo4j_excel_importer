import { useAppStore } from '@/store'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/components/ui/dialog'

export default function ConfirmDialog() {
  const open = useAppStore((s) => s.confirmOpen)
  const message = useAppStore((s) => s.confirmMessage)
  const handleResult = useAppStore((s) => s._handleConfirm)

  return (
    <Dialog open={open} onOpenChange={(v) => !v && handleResult(false)}>
      <DialogContent className="sm:max-w-sm font-mono" showCloseButton={false}>
        <DialogHeader>
          <DialogTitle className="text-sm font-bold">确认操作</DialogTitle>
          <DialogDescription className="text-xs whitespace-pre-line">{message}</DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline" size="sm" onClick={() => handleResult(false)}>
            取消
          </Button>
          <Button size="sm" onClick={() => handleResult(true)}>
            确认
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
