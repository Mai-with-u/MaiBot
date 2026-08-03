/**
 * 模型列表 - 移动端卡片视图
 */
import React from 'react'
import { Loader2, Pencil, Trash2, Zap } from 'lucide-react'

import type { ModelTestResult } from '@/lib/config-api'
import { Button } from '@/components/ui/button'
import { StreamlineIcon } from '@/components/ui/streamline-icon'

import type { ModelInfo } from '../types'

interface ModelCardListProps {
  /** 当前页显示的模型 (分页后的) */
  paginatedModels: ModelInfo[]
  /** 所有模型列表 (未分页) */
  allModels: ModelInfo[]
  /** 编辑模型回调 */
  onEdit: (model: ModelInfo, index: number) => void
  /** 删除模型回调 */
  onDelete: (index: number) => void
  /** 测试模型回调 */
  onTest: (modelName: string) => void
  /** 检查模型是否被使用 */
  isModelUsed: (modelName: string) => boolean
  /** 正在测试的模型名称集合 */
  testingModels: Set<string>
  /** 模型测试结果 */
  modelTestResults: Map<string, ModelTestResult>
  /** 搜索关键词 */
  searchQuery: string
}

function getModelTestStatus(result: ModelTestResult | undefined, isTesting: boolean) {
  if (isTesting) {
    return {
      description: '正在测试模型能力',
      className: 'border-amber-500 animate-pulse',
    }
  }

  if (!result) {
    return {
      description: '未测试：尚未执行模型能力测试',
      className: 'border-transparent',
    }
  }

  if (result.success) {
    return {
      description: `测试通过：文本${result.visual_tested ? '、视觉' : ''}与工具调用正常${
        result.latency_ms != null ? `，耗时 ${(result.latency_ms / 1000).toFixed(2)}s` : ''
      }`,
      className: 'border-green-500',
    }
  }

  return {
    description: result.error || '模型能力测试未通过',
    className: 'border-red-500',
  }
}

export const ModelCardList = React.memo(function ModelCardList({
  paginatedModels,
  allModels,
  onEdit,
  onDelete,
  onTest,
  isModelUsed,
  testingModels,
  modelTestResults,
  searchQuery,
}: ModelCardListProps) {
  if (paginatedModels.length === 0) {
    return (
      <div className="text-muted-foreground bg-card rounded-lg border py-8 text-center md:hidden">
        {searchQuery ? '未找到匹配的模型' : '暂无模型配置'}
      </div>
    )
  }

  return (
    <div className="space-y-2.5 md:hidden">
      {paginatedModels.map((model, displayIndex) => {
        const actualIndex = allModels.findIndex((m) => m === model)
        const used = isModelUsed(model.name)
        const isTesting = testingModels.has(model.name)
        const testResult = modelTestResults.get(model.name)
        const testStatus = getModelTestStatus(testResult, isTesting)
        return (
          <div key={displayIndex} className="bg-card space-y-2 rounded-lg border p-3">
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0 flex-1">
                <div className="mb-1 flex items-center gap-2">
                  <h3
                    className={`w-fit max-w-full truncate border-b-2 pb-0.5 text-sm font-semibold ${testStatus.className}`}
                    title={testStatus.description}
                    aria-label={testStatus.description}
                  >
                    {model.name}
                  </h3>
                  <span
                    className={`block h-3 w-3 shrink-0 rounded-full border ${
                      used
                        ? 'border-green-500 bg-green-500 shadow-[0_0_0_3px_rgba(34,197,94,0.18)]'
                        : 'border-green-700/40 bg-green-950/20'
                    }`}
                    title={used ? '已使用' : '未使用'}
                    aria-label={used ? '已使用' : '未使用'}
                  />
                </div>
                <p
                  className="text-muted-foreground text-[11px] leading-snug break-all"
                  title={model.model_identifier}
                >
                  {model.model_identifier}
                </p>
              </div>
              <div className="flex shrink-0 gap-1">
                <Button
                  variant="outline"
                  size="icon"
                  className="h-8 w-8"
                  onClick={() => onTest(model.name)}
                  disabled={isTesting}
                  title="测试模型"
                  aria-label={`测试模型 ${model.name}`}
                >
                  {isTesting ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <Zap className="h-3.5 w-3.5" />
                  )}
                </Button>
                <Button
                  variant="default"
                  size="icon"
                  className="h-8 w-8"
                  onClick={() => onEdit(model, actualIndex)}
                  title="编辑"
                  aria-label={`编辑模型 ${model.name}`}
                >
                  <StreamlineIcon name="edit-pdf-solid" fallback={Pencil} className="h-3.5 w-3.5" />
                </Button>
                <Button
                  size="icon"
                  onClick={() => onDelete(actualIndex)}
                  className="h-8 w-8 bg-red-600 text-white hover:bg-red-700"
                  title="删除"
                  aria-label={`删除模型 ${model.name}`}
                >
                  <StreamlineIcon name="delete-2-solid" fallback={Trash2} className="h-3.5 w-3.5" />
                </Button>
              </div>
            </div>
            <div className="grid grid-cols-2 gap-x-2 gap-y-1.5 text-xs">
              <div>
                <span className="text-muted-foreground text-xs">提供商</span>
                <p className="truncate font-medium">{model.api_provider}</p>
              </div>
              <div>
                <span className="text-muted-foreground text-xs">视觉</span>
                <p className="flex h-5 items-center">
                  <span
                    className={`block h-3 w-3 rounded-full border ${
                      model.visual
                        ? 'border-green-500 bg-green-500 shadow-[0_0_0_3px_rgba(34,197,94,0.18)]'
                        : 'border-green-700/40 bg-green-950/20'
                    }`}
                    title={model.visual ? '已启用视觉' : '未启用视觉'}
                    aria-label={model.visual ? '已启用视觉' : '未启用视觉'}
                  />
                </p>
              </div>
              <div>
                <span className="text-muted-foreground text-xs">输入价格</span>
                <p className="font-medium">¥{model.price_in}/M</p>
              </div>
              <div>
                <span className="text-muted-foreground text-xs">输出价格</span>
                <p className="font-medium">¥{model.price_out}/M</p>
              </div>
            </div>
          </div>
        )
      })}
    </div>
  )
})
