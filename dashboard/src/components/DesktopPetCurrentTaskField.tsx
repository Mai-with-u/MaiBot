import { useEffect, useState } from 'react'
import type { FieldHookComponentProps } from '@/lib/field-hooks'
import { backendApi } from '@/lib/http'
import { Switch } from '@/components/ui/switch'

export function DesktopPetCurrentTaskField({ value, onChange }: FieldHookComponentProps) {
  const [available, setAvailable] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  useEffect(() => {
    let disposed = false
    const refresh = async () => {
      try {
        const result = await backendApi.get<{ fast_model_configured: boolean }>('/api/webui/desktop-pet/capabilities')
        if (!disposed) { setAvailable(result.fast_model_configured); setError('') }
      } catch (error) {
        if (!disposed) { setAvailable(false); setError(String(error)) }
      } finally { if (!disposed) setLoading(false) }
    }
    void refresh()
    window.addEventListener('focus', refresh)
    return () => { disposed = true; window.removeEventListener('focus', refresh) }
  }, [])
  return <div className="space-y-2">
    <Switch aria-label="显示桌宠手中事" checked={available && value === true}
      disabled={loading || !available} onCheckedChange={checked => onChange?.(checked)} />
    <p className="text-xs text-muted-foreground">
      {loading ? '正在检查 fast_model 配置…' : error ? `无法检查模型配置：${error}` : !available
        ? '请先在模型配置中为 fast_model 分配模型，再开启此选项。'
        : '在桌宠底部显示当前动作，使用像素字幕逐字跳动。'}
    </p>
  </div>
}
