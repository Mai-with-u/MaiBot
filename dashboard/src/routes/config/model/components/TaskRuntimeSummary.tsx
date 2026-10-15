import { useEffect, useState } from 'react'

import { getBotConfigCached } from '@/lib/config-api'

import type { ModelInfo } from '../types'

type VisualMode = 'text' | 'multimodal' | 'auto'
type SummaryTaskName = 'planner' | 'replyer'
type ThinkingState = 'on' | 'off' | 'unset'

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function readVisualMode(botConfig: Record<string, unknown> | null, taskName: SummaryTaskName) {
  const visual = isRecord(botConfig?.visual) ? botConfig.visual : {}
  const value = visual[`${taskName}_mode`]
  return value === 'text' || value === 'multimodal' || value === 'auto' ? (value as VisualMode) : 'auto'
}

/** 从模型 extra_params 推断思考开关；未写入相关参数时视为跟随服务商默认 */
function readThinkingState(extraParams: Record<string, unknown> | undefined): ThinkingState {
  const params = extraParams ?? {}
  if (isRecord(params.thinking) && typeof params.thinking.type === 'string') {
    return params.thinking.type === 'disabled' ? 'off' : 'on'
  }
  if (typeof params.enable_thinking === 'boolean') {
    return params.enable_thinking ? 'on' : 'off'
  }
  if (typeof params.thinking_budget === 'number') {
    return params.thinking_budget === 0 ? 'off' : 'on'
  }
  const effort = isRecord(params.reasoning) ? params.reasoning.effort : params.reasoning_effort
  if (typeof effort === 'string') {
    return effort === 'none' ? 'off' : 'on'
  }
  return 'unset'
}

/** 与后端 planner/replyer 的视觉模式解析保持一致 */
function describeVisualMode(mode: VisualMode, taskName: SummaryTaskName, taskModels: ModelInfo[]) {
  if (mode === 'text') return '文本'
  const visualCount = taskModels.filter((model) => model.visual).length
  if (mode === 'multimodal') {
    return visualCount === taskModels.length ? '多模态' : '多模态（存在未开启视觉的模型）'
  }
  if (taskModels.length === 0 || visualCount === 0) return '文本（自动）'
  if (visualCount === taskModels.length) return '多模态（自动）'
  // planner 要求全部模型支持视觉；replyer 按每次选中的模型决定
  return taskName === 'planner' ? '文本（自动，部分模型未开启视觉）' : '随模型切换（自动）'
}

function describeThinking(taskModels: ModelInfo[]) {
  if (taskModels.length === 0) return '—'
  const states = taskModels.map((model) => readThinkingState(model.extra_params))
  if (states.every((state) => state === 'on')) return '开启'
  if (states.every((state) => state === 'off')) return '关闭'
  if (states.every((state) => state === 'unset')) return '跟随服务商默认'
  return '部分开启'
}

export function TaskRuntimeSummary({
  taskName,
  modelList,
  models,
}: {
  taskName: SummaryTaskName
  modelList: string[]
  models: ModelInfo[]
}) {
  const [botConfig, setBotConfig] = useState<Record<string, unknown> | null>(null)

  useEffect(() => {
    let active = true
    getBotConfigCached()
      .then((config) => {
        if (active) setBotConfig(config)
      })
      .catch(() => {
        if (active) setBotConfig(null)
      })
    return () => {
      active = false
    }
  }, [])

  const modelsByName = new Map(models.map((model) => [model.name, model]))
  const taskModels = modelList
    .map((name) => modelsByName.get(name.trim()))
    .filter((model): model is ModelInfo => Boolean(model))

  const rows = [
    { label: '模式', value: describeVisualMode(readVisualMode(botConfig, taskName), taskName, taskModels) },
    { label: '思考', value: describeThinking(taskModels) },
  ]

  return (
    <dl className="space-y-2 border-t pt-4 text-sm" aria-label={`${taskName} 运行方式`}>
      {rows.map((row) => (
        <div key={row.label} className="flex items-baseline gap-3">
          <dt className="text-muted-foreground w-10 shrink-0">{row.label}</dt>
          <dd className="font-medium">{row.value}</dd>
        </div>
      ))}
    </dl>
  )
}
