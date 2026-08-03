/**
 * 模型列表 - 桌面端表格视图
 */
import React from 'react'
import { Loader2, Pencil, Trash2, Zap } from 'lucide-react'

import type { ModelTestResult } from '@/lib/config-api'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { StreamlineIcon } from '@/components/ui/streamline-icon'

import type { ModelInfo } from '../types'

interface ModelTableProps {
  /** 当前页显示的模型 (分页后的) */
  paginatedModels: ModelInfo[]
  /** 所有模型列表 (未分页) */
  allModels: ModelInfo[]
  /** 过滤后的模型列表 */
  filteredModels: ModelInfo[]
  /** 已选中的模型索引集合 */
  selectedModels: Set<number>
  /** 编辑模型回调 */
  onEdit: (model: ModelInfo, index: number) => void
  /** 删除模型回调 */
  onDelete: (index: number) => void
  /** 测试模型回调 */
  onTest: (modelName: string) => void
  /** 切换选中状态回调 */
  onToggleSelection: (index: number) => void
  /** 切换全选回调 */
  onToggleSelectAll: () => void
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

export const ModelTable = React.memo(function ModelTable({
  paginatedModels,
  allModels,
  filteredModels,
  selectedModels,
  onEdit,
  onDelete,
  onTest,
  onToggleSelection,
  onToggleSelectAll,
  isModelUsed,
  testingModels,
  modelTestResults,
  searchQuery,
}: ModelTableProps) {
  return (
    <div
      data-model-config-table-surface="true"
      className="hidden overflow-hidden rounded-lg border bg-transparent md:block"
    >
      <div className="overflow-x-auto">
        <Table aria-label="模型列表">
          <TableHeader>
            <TableRow>
              <TableHead className="w-12">
                <Checkbox
                  checked={
                    selectedModels.size === filteredModels.length && filteredModels.length > 0
                  }
                  onCheckedChange={onToggleSelectAll}
                />
              </TableHead>
              <TableHead className="w-14 text-center">使用</TableHead>
              <TableHead>模型名称</TableHead>
              <TableHead>模型标识符</TableHead>
              <TableHead>提供商</TableHead>
              <TableHead className="w-14 text-center">视觉</TableHead>
              <TableHead className="text-center">温度</TableHead>
              <TableHead className="text-right">输入价格</TableHead>
              <TableHead className="text-right">输出价格</TableHead>
              <TableHead className="text-right">操作</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {paginatedModels.length === 0 ? (
              <TableRow>
                <TableCell colSpan={10} className="text-muted-foreground py-8 text-center">
                  {searchQuery ? '未找到匹配的模型' : '暂无模型配置'}
                </TableCell>
              </TableRow>
            ) : (
              paginatedModels.map((model, displayIndex) => {
                const actualIndex = allModels.findIndex((m) => m === model)
                const used = isModelUsed(model.name)
                const isTesting = testingModels.has(model.name)
                const testResult = modelTestResults.get(model.name)
                const testStatus = getModelTestStatus(testResult, isTesting)
                return (
                  <TableRow key={displayIndex}>
                    <TableCell>
                      <Checkbox
                        checked={selectedModels.has(actualIndex)}
                        onCheckedChange={() => onToggleSelection(actualIndex)}
                      />
                    </TableCell>
                    <TableCell className="text-center">
                      <span
                        className={`mx-auto block h-3 w-3 rounded-full border ${
                          used
                            ? 'border-green-500 bg-green-500 shadow-[0_0_0_3px_rgba(34,197,94,0.18)]'
                            : 'border-green-700/40 bg-green-950/20'
                        }`}
                        title={used ? '已使用' : '未使用'}
                        aria-label={used ? '已使用' : '未使用'}
                      />
                    </TableCell>
                    <TableCell className="font-medium">
                      <span
                        className={`inline-block border-b-2 pb-0.5 ${testStatus.className}`}
                        title={testStatus.description}
                        aria-label={testStatus.description}
                      >
                        {model.name}
                      </span>
                    </TableCell>
                    <TableCell className="max-w-xs truncate" title={model.model_identifier}>
                      {model.model_identifier}
                    </TableCell>
                    <TableCell>{model.api_provider}</TableCell>
                    <TableCell className="text-center">
                      <span
                        className={`mx-auto block h-3 w-3 rounded-full border ${
                          model.visual
                            ? 'border-green-500 bg-green-500 shadow-[0_0_0_3px_rgba(34,197,94,0.18)]'
                            : 'border-green-700/40 bg-green-950/20'
                        }`}
                        title={model.visual ? '已启用视觉' : '未启用视觉'}
                        aria-label={model.visual ? '已启用视觉' : '未启用视觉'}
                      />
                    </TableCell>
                    <TableCell className="text-center">
                      {model.temperature != null ? (
                        model.temperature
                      ) : (
                        <span className="text-muted-foreground">-</span>
                      )}
                    </TableCell>
                    <TableCell className="text-right">¥{model.price_in}/M</TableCell>
                    <TableCell className="text-right">¥{model.price_out}/M</TableCell>
                    <TableCell className="text-right">
                      <div className="flex justify-end gap-2">
                        <Button
                          variant="outline"
                          size="icon"
                          onClick={() => onTest(model.name)}
                          disabled={isTesting}
                          title="测试模型"
                          aria-label={`测试模型 ${model.name}`}
                        >
                          {isTesting ? (
                            <Loader2 className="h-4 w-4 animate-spin" />
                          ) : (
                            <Zap className="h-4 w-4" />
                          )}
                        </Button>
                        <Button
                          variant="default"
                          size="icon"
                          onClick={() => onEdit(model, actualIndex)}
                          title="编辑"
                          aria-label={`编辑模型 ${model.name}`}
                        >
                          <StreamlineIcon
                            name="edit-pdf-solid"
                            fallback={Pencil}
                            className="h-4 w-4"
                          />
                        </Button>
                        <Button
                          size="icon"
                          onClick={() => onDelete(actualIndex)}
                          className="bg-red-600 text-white hover:bg-red-700"
                          title="删除"
                          aria-label={`删除模型 ${model.name}`}
                        >
                          <StreamlineIcon
                            name="delete-2-solid"
                            fallback={Trash2}
                            className="h-4 w-4"
                          />
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                )
              })
            )}
          </TableBody>
        </Table>
      </div>
    </div>
  )
})
