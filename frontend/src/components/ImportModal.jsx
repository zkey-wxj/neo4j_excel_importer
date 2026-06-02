import { useRef } from 'react'
import { useAppStore } from '@/store'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/components/ui/dialog'
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group'
import { Label } from '@/components/ui/label'

/**
 * 导入弹窗组件
 * 支持选择 Excel 或 JSON 文件导入图谱数据
 * 提供两种导入模式：
 *   - 合并导入：按 nid 合并，已存在更新、不存在新增，不删除已有数据
 *   - 覆盖导入：清空当前分组所有数据后重新导入（不可撤销）
 */
export default function ImportModal() {
  const showImportModal = useAppStore((s) => s.showImportModal)
  const setShowImportModal = useAppStore((s) => s.setShowImportModal)
  const importFile = useAppStore((s) => s.importFile)
  const setImportFile = useAppStore((s) => s.setImportFile)
  const importMode = useAppStore((s) => s.importMode)
  const setImportMode = useAppStore((s) => s.setImportMode)
  const groupId = useAppStore((s) => s.groupId)
  const loadGroup = useAppStore((s) => s.loadGroup)
  const setLoading = useAppStore((s) => s.setLoading)
  const setStatus = useAppStore((s) => s.setStatus)
  const confirm = useAppStore((s) => s.confirm)
  const fileInputRef = useRef(null)

  /** 确认导入：校验参数 → 二次确认 → 上传文件 → 刷新图谱 */
  const handleConfirm = async () => {
    if (!importFile) return
    const gid = groupId
    if (!gid) {
      setStatus('请先输入 group_id', true)
      return
    }
    const modeLabel = importMode === 'override' ? '覆盖导入（将清空现有数据）' : '合并导入'
    if (!await confirm(`确认以「${modeLabel}」方式导入 ${importFile.name} 到分组 ${gid}？`)) return

    setShowImportModal(false)
    setLoading(true)
    try {
      const fd = new FormData()
      fd.append('file', importFile)
      const m = window.location.pathname.match(/^\/e\/[^/]+/)
      const base = m ? m[0] : ''
      const url = `${base}/group-graph/api/import?group_id=${encodeURIComponent(gid)}&mode=${importMode}`
      const res = await fetch(url, { method: 'POST', body: fd })
      const data = await res.json()
      if (!res.ok || data.error) {
        const { toast } = await import('sonner')
        toast.error(`导入失败: ${data.error || res.status}`)
      } else {
        const { toast } = await import('sonner')
        toast.success(`导入成功: 节点 ${data.nodes_imported}，关系 ${data.relations_imported}，跳过 ${data.relations_skipped}`)
        await loadGroup(gid)
      }
    } catch (err) {
      const { toast } = await import('sonner')
      toast.error(`导入失败: ${err.message}`)
    } finally {
      setLoading(false)
      setImportFile(null)
    }
  }

  /** 取消导入：清理文件引用并关闭弹窗 */
  const handleCancel = () => {
    setImportFile(null)
    setShowImportModal(false)
  }

  return (
    <Dialog open={showImportModal} onOpenChange={(open) => !open && handleCancel()}>
      <DialogContent className="sm:max-w-md font-mono">
        <DialogHeader>
          <DialogTitle className="text-sm font-bold">导入图谱数据</DialogTitle>
          <DialogDescription className="text-[11px]">
            {importFile ? `文件: ${importFile.name}` : '未选择文件'}
          </DialogDescription>
        </DialogHeader>

        <RadioGroup
          value={importMode}
          onValueChange={setImportMode}
          className="gap-3"
        >
          <Label
            className={cn(
              'flex gap-3 items-start p-3 rounded-lg border cursor-pointer transition-all',
              importMode === 'merge'
                ? 'border-primary/40 bg-primary/5'
                : 'border-border hover:bg-muted/30'
            )}
          >
            <RadioGroupItem value="merge" className="mt-0.5" />
            <div>
              <div className="text-xs font-semibold text-foreground">合并导入</div>
              <div className="text-[10px] text-muted-foreground mt-1">
                按节点 nid 合并，已存在则更新，不存在则新增；关系按源节点+目标节点合并。不删除已有数据。
              </div>
            </div>
          </Label>
          <Label
            className={cn(
              'flex gap-3 items-start p-3 rounded-lg border cursor-pointer transition-all',
              importMode === 'override'
                ? 'border-destructive/40 bg-destructive/5'
                : 'border-border hover:bg-muted/30'
            )}
          >
            <RadioGroupItem value="override" className="mt-0.5" />
            <div>
              <div className="text-xs font-semibold text-foreground">覆盖导入</div>
              <div className="text-[10px] text-muted-foreground mt-1">
                清空当前分组所有数据后重新导入。此操作不可撤销。
              </div>
            </div>
          </Label>
        </RadioGroup>

        <Button
          variant="outline"
          size="sm"
          className="w-full text-xs"
          disabled={!groupId}
          onClick={() => {
            const gid = groupId
            const m = window.location.pathname.match(/^\/e\/[^/]+/)
            const base = m ? m[0] : ''
            window.open(`${base}/group-graph/api/export?group_id=${encodeURIComponent(gid)}&format=excel`, '_blank')
          }}
        >
          导出 Excel 备份
        </Button>

        <DialogFooter>
          <Button variant="outline" size="sm" onClick={handleCancel}>
            取消
          </Button>
          <Button size="sm" onClick={handleConfirm} disabled={!importFile}>
            确认导入
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
