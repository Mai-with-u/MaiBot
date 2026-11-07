import { Globe, Plus } from 'lucide-react'

import type { TestConnectionResult } from '@/lib/config-api'
import { DashedButton } from '@/components/ui/dashed-button'
import { StatusLight } from '@/components/ui/status-light'
import { DataPanel } from '@/components/ui/surface'
import { cn } from '@/lib/utils'
import '@/styles/model-library.css'
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
      data-provider-sidebar="true"
      className="flex min-h-0 min-w-0 flex-col gap-2 lg:h-full"
      data-config-field-path="api_providers"
    >
      <div data-library-label="true">
        <b>VENDORS</b>
        <h2>模型厂商</h2>
      </div>

      <div className="min-w-0 lg:hidden">
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

      <DataPanel
        padding="none"
        data-library-list="true"
        className="hidden min-h-0 flex-1 overflow-y-auto overscroll-contain lg:block lg:h-0"
      >
        <button
          type="button"
          data-library-row="true"
          data-selected={selectedProvider === '' || undefined}
          onClick={() => onSelectProvider('')}
          aria-pressed={selectedProvider === ''}
          aria-label="全部"
        >
          <StatusLight status={totalModels > 0 ? 'on' : 'off'} glow={false} decorative />
          <span data-library-row-name="true">全部厂商</span>
          <span data-library-row-count="true">{totalModels}</span>
        </button>

        {sortedProviders.map(({ provider }) => {
          const testStatus = getProviderTestStatus(
            testResults.get(provider.name),
            testingProviders.has(provider.name)
          )
          const isSelected = selectedProvider === provider.name
          const count = modelCounts.get(provider.name) ?? 0

          return (
            <div key={provider.name} data-library-row="true" data-selected={isSelected || undefined}>
              <StatusLight status={count > 0 ? 'on' : 'off'} glow={false} decorative />
              <button
                type="button"
                className="min-w-0 text-left"
                onClick={() => onSelectProvider(provider.name)}
                aria-pressed={isSelected}
                aria-label={`筛选厂商 ${provider.name}`}
                title={`${provider.name} · ${provider.base_url}\n${testStatus.description}`}
              >
                <span
                  data-library-row-name="true"
                  className={cn('inline-block max-w-full border-b-2', testStatus.className)}
                >
                  {provider.name}
                </span>
              </button>
              <span data-library-row-count="true">{count}</span>
              <span data-library-row-sub="true">{provider.base_url}</span>
            </div>
          )
        })}
      </DataPanel>
      <DashedButton className="w-full flex-none" data-tour="add-provider-button" onClick={onAdd}>
        <Plus className="size-3.5" aria-hidden="true" />
        添加厂商
      </DashedButton>

      <div data-library-label="true" className="mt-3 max-lg:hidden">
        <b>WEB</b>
        <h2>联网服务</h2>
      </div>
      {/* 联网服务最多露出两行，多了在框里滚动，不挤占厂商列表 */}
      <DataPanel
        padding="none"
        data-library-list="true"
        className="hidden max-h-[7.625rem] flex-none overflow-y-auto overscroll-contain lg:block"
      >
        {webServiceNames.length === 0 && (
          <p className="text-muted-foreground px-3 py-3 text-xs">还没有联网服务</p>
        )}
        {webServiceNames.map((name) => {
          const assigned = assignedWebService === name
          return (
            <button
              key={`web:${name}`}
              data-web-service-row="true"
              data-library-row="true"
              type="button"
              onClick={() => onEditWebSearch?.(name)}
            >
              <StatusLight status={assigned ? 'on' : 'off'} glow={false} decorative />
              <span data-library-row-name="true">{name}</span>
              <Globe className="text-muted-foreground h-3.5 w-3.5 justify-self-end" aria-hidden="true" />
              <span data-library-row-sub="true">联网搜索 · {assigned ? '已分配' : '未分配'}</span>
            </button>
          )
        })}
      </DataPanel>
      {onAddWebSearch && (
        <DashedButton className="w-full flex-none max-lg:hidden" onClick={onAddWebSearch}>
          <Plus className="size-3.5" aria-hidden="true" />
          添加联网服务
        </DashedButton>
      )}
    </aside>
  )
}
