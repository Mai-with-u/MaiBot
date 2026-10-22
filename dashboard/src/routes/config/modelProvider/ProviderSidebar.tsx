import { Globe, Plus } from 'lucide-react'

import type { TestConnectionResult } from '@/lib/config-api'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { cn } from '@/lib/utils'
import type { WebSearchConfig } from '../webSearch/types'

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
  onAddWebSearch?: () => void
  onEditWebSearch?: (name: string) => void
  webConfig?: WebSearchConfig
  assignedWebService?: string
}

export function ProviderSidebar({
  providers,
  modelCounts,
  selectedProvider,
  testingProviders,
  testResults,
  onSelectProvider,
  onAdd,
  onAddWebSearch,
  onEditWebSearch,
  webConfig,
  assignedWebService,
}: ProviderSidebarProps) {
  const webServiceNames = Object.keys(webConfig?.services ?? {})
  const totalModels = Array.from(modelCounts.values()).reduce((total, count) => total + count, 0)
  const sortedProviders = providers
    .map((provider, index) => ({ provider, index }))
    .sort((a, b) => {
      const countDifference =
        (modelCounts.get(b.provider.name) ?? 0) - (modelCounts.get(a.provider.name) ?? 0)
      return countDifference || a.index - b.index
    })

  return (
    <aside
      className="bg-card/30 flex min-h-0 min-w-0 flex-col overflow-hidden rounded-lg border lg:h-full"
      data-config-field-path="api_providers"
    >
      <div className="flex items-center justify-between gap-2 border-b px-3 py-2.5">
        <h2 className="text-sm font-semibold">模型厂商</h2>
        <div className="flex shrink-0 gap-1">
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                type="button"
                variant="outline"
                size="icon"
                className="h-8 w-8"
                title="添加厂商"
                aria-label="添加厂商"
                data-tour="add-provider-button"
              >
                <Plus className="h-4 w-4" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onSelect={onAdd} data-tour="add-llm-provider-item">
                添加 LLM 模型提供商
              </DropdownMenuItem>
              <DropdownMenuItem onSelect={onAddWebSearch}>添加联网搜索</DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>

      <div className="min-w-0 p-2 lg:hidden">
        <select
          className="border-input bg-background h-9 w-full min-w-0 rounded-md border px-3 text-sm"
          aria-label="模型厂商"
          value={selectedProvider ? `provider:${selectedProvider}` : ''}
          onChange={(event) => {
            const value = event.target.value
            if (value.startsWith('web:')) {
              onEditWebSearch?.(value.slice(4))
            } else {
              onSelectProvider(value.slice(9))
            }
          }}
        >
          <option value="">全部 · {totalModels}</option>
          {sortedProviders.map(({ provider }) => (
            <option key={`provider:${provider.name}`} value={`provider:${provider.name}`}>
              {provider.name} · {modelCounts.get(provider.name) ?? 0}
            </option>
          ))}
          {webServiceNames.map((name) => (
            <option key={`web:${name}`} value={`web:${name}`}>
              {name} · 联网搜索
            </option>
          ))}
        </select>
      </div>

      <div className="hidden min-h-0 flex-1 space-y-1 overflow-y-auto overscroll-contain p-2 lg:block lg:h-0">
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

        {sortedProviders.map(({ provider }) => {
          const testStatus = getProviderTestStatus(
            testResults.get(provider.name),
            testingProviders.has(provider.name)
          )
          const isSelected = selectedProvider === provider.name

          return (
            <div
              key={provider.name}
              className={cn(
                'group flex min-w-0 items-center gap-1 rounded-md px-3 py-2 text-sm transition-colors',
                isSelected ? 'bg-primary text-primary-foreground' : 'hover:bg-muted'
              )}
            >
              <button
                type="button"
                className="min-w-0 flex-1 text-left"
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
                <span
                  className={cn(
                    'mt-0.5 hidden truncate text-[11px] xl:block',
                    isSelected ? 'text-primary-foreground/70' : 'text-muted-foreground'
                  )}
                >
                  {provider.base_url}
                </span>
              </button>
              <span
                className={cn(
                  'w-5 shrink-0 text-right text-xs',
                  isSelected ? 'text-primary-foreground/75' : 'text-muted-foreground'
                )}
              >
                {modelCounts.get(provider.name) ?? 0}
              </span>
            </div>
          )
        })}
        {webServiceNames.map((name) => (
          <button
            key={`web:${name}`}
            data-web-service-row="true"
            type="button"
            className="hover:bg-muted flex w-full min-w-0 items-center gap-2 rounded-md px-3 py-2 text-left text-sm transition-colors"
            onClick={() => onEditWebSearch?.(name)}
          >
            <span className="min-w-0 flex-1">
              <span className="block truncate font-medium">{name}</span>
              <span className="text-muted-foreground block text-xs">
                联网搜索 · {assignedWebService === name ? '已分配' : '未分配'}
              </span>
            </span>
            <Globe className="text-muted-foreground h-4 w-5 shrink-0" />
          </button>
        ))}
      </div>
    </aside>
  )
}
