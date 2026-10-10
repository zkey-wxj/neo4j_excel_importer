import { useState } from 'react'
import { Lock } from 'lucide-react'
import { useAppStore } from '@/store'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

/**
 * 访问密码门
 * 后端配置了 access_password 且当前未持有有效令牌时，全屏拦截页面。
 * 校验通过后由 store 触发图谱数据初始化，本组件随之被 App 卸载。
 */
export default function PasswordGate() {
  const authState = useAppStore((s) => s.authState)
  const authError = useAppStore((s) => s.authError)
  const submitPassword = useAppStore((s) => s.submitPassword)
  const [password, setPassword] = useState('')
  const [submitting, setSubmitting] = useState(false)

  // 鉴权探测期间只显示提示，避免闪现密码表单
  if (authState === 'checking') {
    return (
      <div className="flex items-center justify-center w-full h-dvh graph-bg font-mono text-xs text-muted-foreground">
        正在校验访问权限...
      </div>
    )
  }

  const onSubmit = async (e) => {
    e.preventDefault()
    if (!password || submitting) return
    setSubmitting(true)
    await submitPassword(password)
    setSubmitting(false)
  }

  return (
    <div className="flex items-center justify-center w-full h-dvh graph-bg">
      <form
        onSubmit={onSubmit}
        className="w-[min(92vw,22rem)] rounded-xl border border-border bg-background/85 backdrop-blur-md p-5 font-mono shadow-xl"
      >
        <div className="flex items-center gap-2">
          <Lock className="size-4 text-muted-foreground" />
          <h1 className="text-sm font-bold">访问验证</h1>
        </div>
        <p className="mt-1.5 mb-4 text-xs text-muted-foreground leading-relaxed">
          该图谱页面已启用访问密码，请输入密码后继续。
        </p>

        <Label htmlFor="access-password" className="mb-1.5 text-xs">
          访问密码
        </Label>
        <Input
          id="access-password"
          type="password"
          value={password}
          autoFocus
          autoComplete="current-password"
          disabled={submitting}
          placeholder="请输入访问密码"
          onChange={(e) => setPassword(e.target.value)}
        />

        {authError && <p className="mt-2 text-xs text-destructive">{authError}</p>}

        <Button type="submit" size="sm" className="mt-4 w-full" disabled={submitting || !password}>
          {submitting ? '验证中...' : '进入图谱'}
        </Button>
      </form>
    </div>
  )
}
