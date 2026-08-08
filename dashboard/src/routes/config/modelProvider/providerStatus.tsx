import type { TestConnectionResult } from '@/lib/config-api'
import { AlertCircle, CheckCircle2, Loader2, XCircle } from 'lucide-react'

import { Badge } from '@/components/ui/badge'

export function renderProviderTestStatus(
  result: TestConnectionResult | undefined,
  isTesting: boolean
) {
  if (isTesting) {
    const description = '正在测试厂商连接'
    return (
      <Badge variant="secondary" className="h-6 w-6 justify-center p-0" title={description} aria-label={description}>
        <Loader2 className="h-3.5 w-3.5 animate-spin" />
      </Badge>
    )
  }

  if (!result) {
    const description = '未测试：尚未执行厂商连接测试'
    return (
      <Badge
        variant="outline"
        className="border-muted-foreground/40 h-6 w-6 justify-center bg-transparent p-0"
        title={description}
        aria-label={description}
      />
    )
  }

  if (result.network_ok) {
    if (result.api_key_valid === true) {
      const description = `连接正常：网络可访问，API Key 有效${
        result.latency_ms != null ? `，延迟 ${result.latency_ms}ms` : ''
      }`
      return (
        <Badge className="h-6 w-6 justify-center bg-green-600 p-0 hover:bg-green-700" title={description} aria-label={description}>
          <CheckCircle2 className="h-3.5 w-3.5" />
        </Badge>
      )
    }

    if (result.api_key_valid === false) {
      const description = result.error || '连接异常：网络可访问，但 API Key 无效或已过期'
      return (
        <Badge variant="destructive" className="h-6 w-6 justify-center p-0" title={description} aria-label={description}>
          <AlertCircle className="h-3.5 w-3.5" />
        </Badge>
      )
    }

    const description = `可访问：网络连接正常，但未确认 API Key 是否有效${
      result.latency_ms != null ? `，延迟 ${result.latency_ms}ms` : ''
    }`
    return (
      <Badge className="h-6 w-6 justify-center bg-blue-600 p-0 hover:bg-blue-700" title={description} aria-label={description}>
        <CheckCircle2 className="h-3.5 w-3.5" />
      </Badge>
    )
  }

  const description = result.error || '连接失败：无法访问该厂商'
  return (
    <Badge variant="destructive" className="h-6 w-6 justify-center p-0" title={description} aria-label={description}>
      <XCircle className="h-3.5 w-3.5" />
    </Badge>
  )
}
