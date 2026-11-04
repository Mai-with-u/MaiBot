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
import type { ModelCreationType } from './AddModelMenu'
import '@/styles/model-library.css'
import { ModelTaskSummary } from './TaskAssignmentBoard'

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
  /** 模型负责的工作名称，和「分工」页对应 */
  getModelTasks?: (modelName: string) => string[]
  /** 正在测试的模型名称集合 */
  testingModels: Set<string>
  /** 模型测试结果 */
  modelTestResults: Map<string, ModelTestResult>
  /** 搜索关键词 */
  searchQuery: string
  /** 添加模型回调；添加入口已移到列表工具栏，保留参数兼容旧调用 */
  onAdd?: (type: ModelCreationType) => void
}

// 操作列宽度固定，和表格同一底色；表格宽度跟随容器，三个文本列平分剩余空间，长文本截断。
const ACTION_CELL_CLASS = 'w-24 text-right whitespace-nowrap'
const ACTION_CELL_ATTR = { 'data-model-config-action-cell': 'true' } as const

/** 能力标签：嵌入模型只标嵌入类型，对话模型标出能否看图。 */
function modelCapabilityTags(model: ModelInfo) {
  if (model.model_type === 'embedding') return ['文本嵌入']
  if (model.model_type === 'image_embedding') return ['图片嵌入']
  return model.visual ? ['对话', '视觉'] : ['对话']
}

/** 价格只写数字，单位放在表头；没填写的显示 -。 */
function formatModelPrice(value: number | null | undefined) {
  return value == null ? '-' : String(value)
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
  getModelTasks,
  testingModels,
  modelTestResults,
  searchQuery,
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
              <TableHead className="w-[26%]">模型</TableHead>
              <TableHead className="w-[14%]">厂商</TableHead>
              <TableHead className="w-[12%]">能力</TableHead>
              <TableHead className="w-[13%] whitespace-nowrap">
                价格 <span className="font-normal text-muted-foreground">¥/M</span>
              </TableHead>
              <TableHead className="whitespace-nowrap">负责的工作</TableHead>
              <TableHead {...ACTION_CELL_ATTR} className={ACTION_CELL_CLASS}>
                <span className="sr-only">操作</span>
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {paginatedModels.length === 0 ? (
              <TableRow>
                <TableCell colSpan={7} className="text-muted-foreground py-8 text-center">
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
                    <TableCell>
                      <span
                        className={`block w-fit max-w-full truncate border-b-2 font-mono text-sm ${testStatus.className}`}
                        title={`${model.name}：${testStatus.description}`}
                        aria-label={testStatus.description}
                      >
                        {model.name}
                      </span>
                      <span
                        className="block truncate font-mono text-xs text-muted-foreground"
                        title={model.model_identifier}
                      >
                        {model.model_identifier}
                      </span>
                    </TableCell>
                    <TableCell className="truncate font-mono text-sm" title={model.api_provider}>{model.api_provider}</TableCell>
                    <TableCell>
                      <span
                        data-model-capabilities="true"
                        className="block truncate text-xs text-muted-foreground"
                        aria-label={model.visual ? '已启用视觉' : '未启用视觉'}
                      >
                        {modelCapabilityTags(model).join(' · ')}
                      </span>
                    </TableCell>
                    <TableCell className="whitespace-nowrap font-mono text-sm" title={`输入 ¥${model.price_in}/M，输出 ¥${model.price_out}/M`}>
                      {formatModelPrice(model.price_in)} · {formatModelPrice(model.price_out)}
                      {!!model.price_periods?.length && (
                        <span className="block text-xs font-normal text-primary">
                          {model.price_periods.length} 个价格时段
                        </span>
                      )}
                    </TableCell>
                    <TableCell>
                      <ModelTaskSummary used={used} tasks={getModelTasks?.(model.name) ?? []} />
                    </TableCell>
                    <TableCell {...ACTION_CELL_ATTR} className={ACTION_CELL_CLASS}>
                      <div data-model-row-actions="true">
                        <Button
                          variant="ghost"
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
                          variant="ghost"
                          size="icon-xs"
                          className="shrink-0"
                          onClick={() => onEdit(model, actualIndex)}
                          title="编辑"
                          aria-label={`编辑模型 ${model.name}`}
                        >
                          <Pencil className="h-4 w-4" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon-xs"
                          data-action="delete"
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
