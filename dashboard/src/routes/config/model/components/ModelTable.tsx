/**
 * 模型列表 - 桌面端表格视图
 */
import React from 'react'
import { Loader2, Pencil, Trash2, Zap } from 'lucide-react'

import type { ModelTestResult } from '@/lib/config-api'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { DataPanel } from '@/components/ui/surface'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'

import type { ModelInfo } from '../types'
import { AddModelMenu, type ModelCreationType } from './AddModelMenu'

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
  /** 添加模型回调 */
  onAdd: (type: ModelCreationType) => void
}

// 操作列固定右缘；表格宽度跟随容器，不设置最小宽度，避免固定列覆盖溢出的价格列。
// 状态、价格与操作列保留宽度，三个文本列平分剩余空间，长文本截断。
// 固定列使用不透明背景，retro 主题的纸面实色见 index.css。
const ACTION_CELL_CLASS = 'sticky right-0 z-10 w-28 bg-background text-right whitespace-nowrap'
const ACTION_CELL_ATTR = { 'data-model-config-action-cell': 'true' } as const

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
    const isEmbeddingTest = result.test_kind === 'text_embedding' || result.test_kind === 'image_embedding'
    const summary = isEmbeddingTest
      ? `测试通过：嵌入向量正常${result.embedding_dimension ? `（维度 ${result.embedding_dimension}）` : ''}`
      : result.test_kind === 'replyer'
        ? '测试通过：模拟 replyer 任务回复正常'
        : `测试通过：文本${result.visual_tested ? '、视觉' : ''}与工具调用正常`
    return {
      description: `${summary}${
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
  onAdd,
}: ModelTableProps) {
  return (
    <DataPanel
      data-model-config-table-surface="true"
      padding="none"
      className="hidden overflow-hidden md:block"
    >
        <Table aria-label="模型列表" className="table-fixed min-w-0 [&_th]:px-2 [&_td]:px-2 [&_td]:py-2">
          <TableHeader>
            <TableRow>
              <TableHead className="w-8">
                <Checkbox
                  checked={
                    selectedModels.size === filteredModels.length && filteredModels.length > 0
                  }
                  onCheckedChange={onToggleSelectAll}
                />
              </TableHead>
              <TableHead className="w-10 text-center whitespace-nowrap">使用</TableHead>
              <TableHead>模型名称</TableHead>
              <TableHead>模型标识符</TableHead>
              <TableHead>提供商</TableHead>
              <TableHead className="w-10 text-center whitespace-nowrap">视觉</TableHead>
              <TableHead className="w-20 text-right whitespace-nowrap">默认输入</TableHead>
              <TableHead className="w-20 text-right whitespace-nowrap">默认输出</TableHead>
              <TableHead {...ACTION_CELL_ATTR} className={ACTION_CELL_CLASS}>
                <AddModelMenu
                  onAdd={onAdd}
                  className="h-8 shrink-0"
                  dataTour="add-model-button"
                />
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {paginatedModels.length === 0 ? (
              <TableRow>
                <TableCell colSpan={9} className="text-muted-foreground py-8 text-center">
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
                        className={`block truncate border-b-2 pb-0.5 ${testStatus.className}`}
                        title={`${model.name}：${testStatus.description}`}
                        aria-label={testStatus.description}
                      >
                        {model.name}
                      </span>
                      {!!model.price_periods?.length && (
                        <span className="block truncate text-xs font-normal text-muted-foreground">
                          {model.price_periods.length} 个价格时段
                        </span>
                      )}
                    </TableCell>
                    <TableCell className="max-w-xs truncate" title={model.model_identifier}>
                      {model.model_identifier}
                    </TableCell>
                    <TableCell className="truncate" title={model.api_provider}>{model.api_provider}</TableCell>
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
                    <TableCell className="truncate text-right" title={`¥${model.price_in}/M`}>¥{model.price_in}/M</TableCell>
                    <TableCell className="truncate text-right" title={`¥${model.price_out}/M`}>¥{model.price_out}/M</TableCell>
                    <TableCell {...ACTION_CELL_ATTR} className={ACTION_CELL_CLASS}>
                      <div className="flex justify-end gap-1">
                        <Button
                          variant="outline"
                          size="icon-xs"
                          className="shrink-0"
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
                          variant="outline"
                          size="icon-xs"
                          className="shrink-0"
                          onClick={() => onEdit(model, actualIndex)}
                          title="编辑"
                          aria-label={`编辑模型 ${model.name}`}
                        >
                          <Pencil className="h-4 w-4" />
                        </Button>
                        <Button
                          variant="destructive"
                          size="icon-xs"
                          className="shrink-0"
                          onClick={() => onDelete(actualIndex)}
                          title="删除"
                          aria-label={`删除模型 ${model.name}`}
                        >
                          <Trash2 className="h-4 w-4" />
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                )
              })
            )}
          </TableBody>
        </Table>
    </DataPanel>
  )
})
