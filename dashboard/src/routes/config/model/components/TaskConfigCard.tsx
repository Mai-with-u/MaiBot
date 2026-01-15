/**
 * 任务配置卡片组件
 */
import React from 'react'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Slider } from '@/components/ui/slider'
import { MultiSelect } from '@/components/ui/multi-select'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import type { TaskConfig } from '../types'

interface TaskConfigCardProps {
  title: string
  description: string
  taskConfig: TaskConfig
  modelNames: string[]
  onChange: (field: keyof TaskConfig, value: string[] | number | string) => void
  hideTemperature?: boolean
  hideMaxTokens?: boolean
  dataTour?: string
}

export const TaskConfigCard = React.memo(function TaskConfigCard({
  title,
  description,
  taskConfig,
  modelNames,
  onChange,
  hideTemperature = false,
  hideMaxTokens = false,
  dataTour,
}: TaskConfigCardProps) {
  const handleModelChange = (values: string[]) => {
    onChange('model_list', values)
  }

  return (
    <div className="rounded-lg border bg-card p-4 sm:p-6 space-y-4">
      <div>
        <h4 className="font-semibold text-base sm:text-lg">{title}</h4>
        <p className="text-xs sm:text-sm text-muted-foreground mt-1">{description}</p>
      </div>

      <div className="grid gap-4">
        {/* 模型列表 */}
        <div className="grid gap-2" data-tour={dataTour}>
          <Label>模型列表</Label>
          <MultiSelect
            options={modelNames.map((name) => ({ label: name, value: name }))}
            selected={taskConfig.model_list || []}
            onChange={handleModelChange}
            placeholder="选择模型..."
            emptyText="暂无可用模型"
          />
        </div>

        {/* 温度和最大 Token */}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          {!hideTemperature && (
            <div className="grid gap-3">
              <div className="flex items-center justify-between">
                <Label>温度</Label>
                <Input
                  type="number"
                  step="0.1"
                  min="0"
                  max="1"
                  value={taskConfig.temperature ?? 0.3}
                  onChange={(e) => {
                    const value = parseFloat(e.target.value)
                    if (!isNaN(value) && value >= 0 && value <= 1) {
                      onChange('temperature', value)
                    }
                  }}
                  className="w-20 h-8 text-sm"
                />
              </div>
              <Slider
                value={[taskConfig.temperature ?? 0.3]}
                onValueChange={(values) => onChange('temperature', values[0])}
                min={0}
                max={1}
                step={0.1}
                className="w-full"
              />
            </div>
          )}

          {!hideMaxTokens && (
            <div className="grid gap-2">
              <Label>最大 Token</Label>
              <Input
                type="number"
                step="1"
                min="1"
                value={taskConfig.max_tokens ?? 1024}
                onChange={(e) => onChange('max_tokens', parseInt(e.target.value))}
              />
            </div>
          )}
        </div>

        {/* 慢请求阈值 */}
        <div className="grid gap-2">
          <div className="flex items-center justify-between">
            <Label>慢请求阈值 (秒)</Label>
            <span className="text-xs text-muted-foreground">超时警告</span>
          </div>
          <Input
            type="number"
            step="1"
            min="1"
            value={taskConfig.slow_threshold ?? 15}
            onChange={(e) => {
              const value = parseInt(e.target.value)
              if (!isNaN(value) && value >= 1) {
                onChange('slow_threshold', value)
              }
            }}
            placeholder="15"
          />
          <p className="text-xs text-muted-foreground">
            模型响应时间超过此阈值将输出警告日志
          </p>
        </div>

        {/* 模型选择策略 */}
        <div className="grid gap-2">
          <Label>模型选择策略</Label>
          <Select
            value={taskConfig.selection_strategy ?? 'balance'}
            onValueChange={(value) => onChange('selection_strategy', value)}
          >
            <SelectTrigger>
              <SelectValue placeholder="选择模型选择策略" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="balance">负载均衡（balance）</SelectItem>
              <SelectItem value="random">随机选择（random）</SelectItem>
            </SelectContent>
          </Select>
          <p className="text-xs text-muted-foreground">
            负载均衡：优先选择使用次数少的模型。随机选择：完全随机从模型列表中选择
          </p>
        </div>
      </div>
    </div>
  )
})
