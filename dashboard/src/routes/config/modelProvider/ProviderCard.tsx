import type { TestConnectionResult } from '@/lib/config-api'
import { AlertCircle, CheckCircle2, Loader2, Pencil, Trash2, XCircle, Zap } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'

import type { APIProvider } from './types'

interface ProviderCardProps {
  provider: APIProvider
  actualIndex: number
  testingProviders: Set<string>
  testResults: Map<string, TestConnectionResult>
  onEdit: (provider: APIProvider, index: number) => void
  onDelete: (index: number) => void
  onTest: (name: string) => void
}

export function ProviderCard({
  provider,
  actualIndex,
  testingProviders,
  testResults,
  onEdit,
  onDelete,
  onTest,
}: ProviderCardProps) {
  const renderTestStatus = () => {
    const isTesting = testingProviders.has(provider.name)
    const result = testResults.get(provider.name)

    if (isTesting) {
      return (
        <Badge variant="secondary" className="gap-1">
          <Loader2 className="h-3 w-3 animate-spin" />
          测试中
        </Badge>
      )
    }

    if (!result) return null

    if (result.network_ok) {
      if (result.api_key_valid === true) {
        return (
          <Badge className="gap-1 bg-green-600 hover:bg-green-700">
            <CheckCircle2 className="h-3 w-3" />
            正常
          </Badge>
        )
      } else if (result.api_key_valid === false) {
        return (
          <Badge variant="destructive" className="gap-1">
            <AlertCircle className="h-3 w-3" />
            Key无效
          </Badge>
        )
      } else {
        return (
          <Badge className="gap-1 bg-blue-600 hover:bg-blue-700">
            <CheckCircle2 className="h-3 w-3" />
            可访问
          </Badge>
        )
      }
    } else {
      return (
        <Badge variant="destructive" className="gap-1">
          <XCircle className="h-3 w-3" />
          离线
        </Badge>
      )
    }
  }

  return (
    <div className="rounded-lg border bg-card p-4 space-y-3">
      <div className="flex items-start justify-between gap-2">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <h3 className="font-semibold text-base truncate">{provider.name}</h3>
            {renderTestStatus()}
          </div>
          <p className="text-xs text-muted-foreground mt-1 break-all">{provider.base_url}</p>
        </div>
        <div className="flex gap-1 flex-shrink-0">
          <Button
            variant="outline"
            size="sm"
            onClick={() => onTest(provider.name)}
            disabled={testingProviders.has(provider.name)}
            title="测试连接"
          >
            {testingProviders.has(provider.name) ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Zap className="h-4 w-4" />
            )}
          </Button>
          <Button
            variant="default"
            size="sm"
            onClick={() => onEdit(provider, actualIndex)}
          >
            <Pencil className="h-4 w-4" strokeWidth={2} fill="none" />
          </Button>
          <Button
            size="sm"
            onClick={() => onDelete(actualIndex)}
            className="bg-red-600 hover:bg-red-700 text-white"
          >
            <Trash2 className="h-4 w-4" strokeWidth={2} fill="none" />
          </Button>
        </div>
      </div>
      <div className="grid grid-cols-2 gap-2 text-sm">
        <div>
          <span className="text-muted-foreground text-xs">客户端类型</span>
          <p className="font-medium">{provider.client_type}</p>
        </div>
        <div>
          <span className="text-muted-foreground text-xs">最大重试</span>
          <p className="font-medium">{provider.max_retry}</p>
        </div>
        <div>
          <span className="text-muted-foreground text-xs">超时(秒)</span>
          <p className="font-medium">{provider.timeout}</p>
        </div>
        <div>
          <span className="text-muted-foreground text-xs">重试间隔(秒)</span>
          <p className="font-medium">{provider.retry_interval}</p>
        </div>
      </div>
    </div>
  )
}
