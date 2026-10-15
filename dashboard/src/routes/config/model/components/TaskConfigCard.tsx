/**
 * 任务配置卡片组件
 */
import React from 'react'
import { Info, X } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Slider } from '@/components/ui/slider'
import { MultiSelect } from '@/components/ui/multi-select'
import { StreamlineIcon } from '@/components/ui/streamline-icon'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip'
import { cn } from '@/lib/utils'

import type { TaskConfig } from '../types'

interface TaskConfigCardProps {
  title: string
  description: string
  taskConfig: TaskConfig
  modelNames: string[]
  onChange: (field: keyof TaskConfig, value: string[] | number | string) => void
  hideTemperature?: boolean
  hideMaxTokens?: boolean
  /** 嵌入类任务不提供选择策略：向量空间必须保持一致 */
  hideSelectionStrategy?: boolean
  advanced?: boolean
  showAdvancedSettings?: boolean
  dataTour?: string
  /** 单选模型任务（如嵌入模型）用下拉单选替代多选标签列表 */
  singleModel?: boolean
}

const selectionStrategyOptions = [
  {
    value: 'balance',
    label: '负载均衡（balance）',
    description: '优先选择当前使用次数较少的模型，适合多个同类模型共同承担请求。',
  },
  {
    value: 'random',
    label: '随机选择（random）',
    description: '每次请求从模型列表中随机选择一个模型，适合简单分散请求。',
  },
  {
    value: 'sequential',
    label: '按顺序优先（sequential）',
    description: '优先使用模型列表中靠前的模型，前面的模型不可用时再尝试后面的模型。',
  },
]

function clampTemperature(value: number): number {
  return Math.min(2, Math.max(0, value))
}

export const TaskConfigCard = React.memo(function TaskConfigCard({
  title,
  description,
  taskConfig,
  modelNames,
  onChange,
  hideTemperature = false,
  hideMaxTokens = false,
  hideSelectionStrategy = false,
  advanced = false,
  showAdvancedSettings = false,
  dataTour,
  singleModel = false,
}: TaskConfigCardProps) {
  const temperatureInputId = React.useId()
  const selectedModels = taskConfig.model_list || []
  const selectedModel = selectedModels[0] ?? ''

  const handleModelChange = (values: string[]) => {
    onChange('model_list', values)
  }

  return (
    <div
      className={cn(
        'space-y-3 pb-5 pt-3 sm:pb-6 sm:pt-4',
        advanced && 'bg-amber-50/30 px-2 dark:bg-amber-500/10',
      )}
    >
      <div className="flex min-w-0 flex-col gap-1 sm:flex-row sm:items-baseline sm:justify-between sm:gap-3">
        <h4 className="flex-none whitespace-nowrap text-base font-semibold sm:text-lg">{title}</h4>
        <p className="min-w-0 text-xs leading-relaxed text-muted-foreground sm:flex-1 sm:truncate sm:text-right sm:text-sm">{description}</p>
      </div>

      <div className="grid gap-3">
        {/* 模型列表 */}
        <div data-tour={dataTour}>
          {singleModel ? (
            <div className="flex min-w-0 items-center gap-2">
              <Select
                value={selectedModel}
                onValueChange={(value) => onChange('model_list', [value])}
              >
                <SelectTrigger className="min-w-0 flex-1">
                  <SelectValue placeholder="选择模型..." />
                </SelectTrigger>
                <SelectContent>
                  {modelNames.map((name) => (
                    <SelectItem key={name} value={name}>
                      {name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {selectedModel && (
                <Button
                  type="button"
                  size="icon"
                  variant="ghost"
                  className="h-8 w-8 flex-none text-muted-foreground hover:text-destructive"
                  aria-label="清除已选模型"
                  onClick={() => onChange('model_list', [])}
                >
                  <X className="h-4 w-4" />
                </Button>
              )}
            </div>
          ) : (
            <MultiSelect
              options={modelNames.map((name) => ({ label: name, value: name }))}
              selected={selectedModels}
              onChange={handleModelChange}
              placeholder="选择模型..."
              emptyText="暂无可用模型"
              compact
            />
          )}
        </div>

        {/* 推理参数 */}
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-1 lg:grid-cols-3">
          {!hideTemperature && (
            <div className="flex min-w-0 flex-col items-stretch gap-2 sm:flex-row sm:items-center sm:gap-3">
              <Label htmlFor={temperatureInputId} className="whitespace-nowrap">温度</Label>
              <Input
                id={temperatureInputId}
                type="number"
                value={taskConfig.temperature ?? 0.7}
                onChange={(e) => {
                  const value = parseFloat(e.target.value)
                  if (!isNaN(value)) {
                    onChange('temperature', clampTemperature(value))
                  }
                }}
                min={0}
                max={2}
                step={0.1}
                className="h-10 w-full min-w-0 text-sm tabular-nums sm:hidden"
              />
              <Slider
                value={[taskConfig.temperature ?? 0.7]}
                onValueChange={(values) => onChange('temperature', values[0])}
                min={0}
                max={2}
                step={0.1}
                className="hidden min-w-0 flex-1 sm:flex"
                data-dashboard-slider="config"
                data-dashboard-slider-value-format="fixed-1"
              />
            </div>
          )}

          {!hideMaxTokens && (
            <div className="flex min-w-0 flex-col items-stretch gap-2 sm:flex-row sm:items-center sm:gap-3">
              <Label className="whitespace-nowrap">最大 Token</Label>
              <Input
                type="number"
                step="1"
                min="1"
                value={taskConfig.max_tokens ?? 4096}
                onChange={(e) => onChange('max_tokens', parseInt(e.target.value))}
                className="h-10 min-w-0 sm:h-9 sm:flex-1"
              />
            </div>
          )}

          {/* 模型选择策略 */}
          {!hideSelectionStrategy && (
          <div className="col-span-2 flex min-w-0 flex-col items-stretch gap-2 sm:col-span-1 sm:flex-row sm:items-center sm:gap-3">
            <Label className="whitespace-nowrap">选择策略</Label>
            <Select
              value={taskConfig.selection_strategy ?? 'balance'}
              onValueChange={(value) => onChange('selection_strategy', value)}
            >
              <SelectTrigger className="h-10 min-w-0 sm:h-9 sm:flex-1">
                <SelectValue placeholder="选择模型选择策略" />
              </SelectTrigger>
              <SelectContent>
                <TooltipProvider delayDuration={150}>
                  {selectionStrategyOptions.map((option) => (
                    <Tooltip key={option.value}>
                      <TooltipTrigger asChild>
                        <SelectItem value={option.value} title={option.description}>
                          {option.label}
                        </SelectItem>
                      </TooltipTrigger>
                      <TooltipContent
                        side="right"
                        align="center"
                        className="max-w-72 bg-background text-foreground border shadow-lg"
                      >
                        {option.description}
                      </TooltipContent>
                    </Tooltip>
                  ))}
                </TooltipProvider>
              </SelectContent>
            </Select>
          </div>
          )}
        </div>

        {showAdvancedSettings && (
          <div className="grid gap-3 rounded-md border border-amber-200 bg-amber-50/50 p-3 dark:border-amber-500/40 dark:bg-amber-500/10 sm:grid-cols-2">
            <div className="flex min-w-0 items-center gap-3">
              <div className="flex shrink-0 items-center gap-1.5">
                <Label>任务硬超时 (秒)</Label>
                <TooltipProvider delayDuration={150}>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <StreamlineIcon
                        name="information-circle-solid"
                        fallback={Info}
                        className="h-3.5 w-3.5 cursor-help text-muted-foreground"
                      />
                    </TooltipTrigger>
                    <TooltipContent side="top" align="start" className="max-w-64">
                      到达此时间会取消本次模型调用，并尝试使用任务中的下一个模型。默认值：240 秒。
                    </TooltipContent>
                  </Tooltip>
                </TooltipProvider>
              </div>
              <Input
                type="number"
                step="1"
                min="1"
                value={taskConfig.hard_timeout ?? ''}
                onChange={(e) => {
                  const value = parseInt(e.target.value)
                  if (!isNaN(value) && value >= 1) {
                    onChange('hard_timeout', value)
                  }
                }}
                placeholder="240"
                className="min-w-0 flex-1"
              />
            </div>
          </div>
        )}

      </div>
    </div>
  )
})
