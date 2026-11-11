import { useEffect, useState } from 'react'
import type { FieldHookComponentProps } from '@/lib/field-hooks'
import { resolveFieldLabel } from '@/lib/config-label'
import { backendApi } from '@/lib/http'
import { Switch } from '@/components/ui/switch'
import type { FieldSchema } from '@/types/config-schema'

export function DesktopPetCurrentTaskField({ value, onChange, schema }: FieldHookComponentProps) {
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
  const field = schema as FieldSchema | undefined
  const label = field?.label ? resolveFieldLabel(field) : '显示桌宠手中事'
  const description = field?.description
  // 可用时说明文字放在标签的悬停提示里，只有不可用或出错时才占一行
  const notice = loading ? '正在检查 fast_model 配置…' : error ? `无法检查模型配置：${error}` : !available
    ? '请先在模型配置中为 fast_model 分配模型。'
    : ''
  return <div className="space-y-1">
    <div className="flex items-center justify-between gap-3">
      <span className={description ? 'cursor-help text-[15px] leading-6' : 'text-[15px] leading-6'} title={description}>{label}</span>
      <Switch aria-label={label} checked={available && value === true}
        disabled={loading || !available} onCheckedChange={checked => onChange?.(checked)} />
    </div>
    {notice && <p className="text-xs text-muted-foreground">{notice}</p>}
  </div>
}
