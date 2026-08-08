import type { TestConnectionResult } from '@/lib/config-api'
import { Loader2, Pencil, Trash2, Zap } from 'lucide-react'

import { Button } from '@/components/ui/button'

import { renderProviderTestStatus } from './providerStatus'
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
    return renderProviderTestStatus(result, isTesting)
  }

  return (
    <div className="bg-card space-y-3 rounded-lg border p-4">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="truncate text-base font-semibold">{provider.name}</h3>
            {renderTestStatus()}
          </div>
          <p className="text-muted-foreground mt-1 text-xs break-all">{provider.base_url}</p>
        </div>
        <div className="flex flex-shrink-0 gap-1">
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
            title="编辑"
            aria-label={`编辑厂商 ${provider.name}`}
          >
            <Pencil className="h-4 w-4" strokeWidth={2} fill="none" />
          </Button>
          <Button
            size="sm"
            onClick={() => onDelete(actualIndex)}
            className="bg-red-600 text-white hover:bg-red-700"
            title="删除"
            aria-label={`删除厂商 ${provider.name}`}
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
          <span className="text-muted-foreground text-xs">重试</span>
          <p className="font-medium">
            {provider.max_retry} 次 / {provider.retry_interval} 秒
          </p>
        </div>
        <div>
          <span className="text-muted-foreground text-xs">超时(秒)</span>
          <p className="font-medium">{provider.timeout}</p>
        </div>
      </div>
    </div>
  )
}
