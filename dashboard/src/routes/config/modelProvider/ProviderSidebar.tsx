import { Loader2, Pencil, Plus, Trash2, Zap } from 'lucide-react'

import type { TestConnectionResult } from '@/lib/config-api'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

import { getProviderTestStatus } from './providerStatus'
import type { APIProvider } from './types'

interface ProviderSidebarProps {
  providers: APIProvider[]
  modelCounts: Map<string, number>
  selectedProvider: string
  testingProviders: Set<string>
  testResults: Map<string, TestConnectionResult>
  onSelectProvider: (providerName: string) => void
  onAdd: () => void
  onEdit: (provider: APIProvider, index: number) => void
  onDelete: (index: number) => void
  onTest: (providerName: string) => void
  onTestAll: () => void
}

export function ProviderSidebar({
  providers,
  modelCounts,
  selectedProvider,
  testingProviders,
  testResults,
  onSelectProvider,
  onAdd,
  onEdit,
  onDelete,
  onTest,
  onTestAll,
}: ProviderSidebarProps) {
  const totalModels = Array.from(modelCounts.values()).reduce((total, count) => total + count, 0)

  return (
    <aside className="bg-card/30 min-w-0 rounded-lg border" data-config-field-path="api_providers">
      <div className="flex items-center justify-between gap-2 border-b px-3 py-2.5">
        <div>
          <h2 className="text-sm font-semibold">模型厂商</h2>
          <p className="text-muted-foreground text-xs">选择后筛选右侧模型</p>
        </div>
        <div className="flex shrink-0 gap-1">
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="h-8 w-8"
            onClick={onTestAll}
            disabled={providers.length === 0 || testingProviders.size > 0}
            title="测试全部连接"
            aria-label="测试全部厂商连接"
          >
            {testingProviders.size > 0 ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Zap className="h-4 w-4" />
            )}
          </Button>
          <Button
            type="button"
            variant="outline"
            size="icon"
            className="h-8 w-8"
            onClick={onAdd}
            title="添加厂商"
            aria-label="添加厂商"
            data-tour="add-provider-button"
          >
            <Plus className="h-4 w-4" />
          </Button>
        </div>
      </div>

      <div className="max-h-[36rem] space-y-1 overflow-y-auto p-2">
        <button
          type="button"
          className={cn(
            'flex w-full items-center justify-between rounded-md px-3 py-2 text-left text-sm transition-colors',
            selectedProvider === '' ? 'bg-primary text-primary-foreground' : 'hover:bg-muted'
          )}
          onClick={() => onSelectProvider('')}
          aria-pressed={selectedProvider === ''}
          aria-label="全部"
        >
          <span className="font-medium">全部</span>
          <span
            className={cn(
              'text-xs',
              selectedProvider === '' ? 'text-primary-foreground/75' : 'text-muted-foreground'
            )}
          >
            {totalModels}
          </span>
        </button>

        {providers.map((provider, index) => {
          const testStatus = getProviderTestStatus(
            testResults.get(provider.name),
            testingProviders.has(provider.name)
          )
          const isSelected = selectedProvider === provider.name

          return (
            <div
              key={provider.name}
              className={cn(
                'group flex min-w-0 items-center gap-1 rounded-md border border-transparent px-2 py-1 transition-colors',
                isSelected ? 'border-primary/30 bg-primary/10' : 'hover:bg-muted/70'
              )}
            >
              <button
                type="button"
                className="min-w-0 flex-1 truncate text-left"
                onClick={() => onSelectProvider(provider.name)}
                aria-pressed={isSelected}
                aria-label={`筛选厂商 ${provider.name}`}
                title={`${provider.name} · ${provider.base_url}\n${testStatus.description}`}
              >
                <span
                  className={cn(
                    'inline-block max-w-full truncate border-b-2 pb-0.5 text-sm font-medium',
                    testStatus.className
                  )}
                >
                  {provider.name}
                </span>
              </button>
              <span className="text-muted-foreground w-5 shrink-0 text-right text-xs">
                {modelCounts.get(provider.name) ?? 0}
              </span>
              <div className="flex shrink-0 gap-0.5">
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="h-7 w-7"
                  onClick={() => onTest(provider.name)}
                  disabled={testingProviders.has(provider.name)}
                  title="测试连接"
                  aria-label={`测试厂商 ${provider.name} 连接`}
                >
                  {testingProviders.has(provider.name) ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <Zap className="h-3.5 w-3.5" />
                  )}
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="h-7 w-7"
                  onClick={() => onEdit(provider, index)}
                  title="编辑"
                  aria-label={`编辑厂商 ${provider.name}`}
                >
                  <Pencil className="h-3.5 w-3.5" />
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="text-destructive hover:text-destructive h-7 w-7"
                  onClick={() => onDelete(index)}
                  title="删除"
                  aria-label={`删除厂商 ${provider.name}`}
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </Button>
              </div>
            </div>
          )
        })}
      </div>
    </aside>
  )
}
