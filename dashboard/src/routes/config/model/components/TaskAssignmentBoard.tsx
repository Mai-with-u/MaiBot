/**
 * 分工页：麦麦的每项工作由哪些模型负责。
 * 基础工作用卡片放在最前面，进阶工作默认收起；点开任一项在右侧抽屉里编辑模型顺序、使用方式和参数。
 */
import { type ReactNode, useState } from 'react'
import { ChevronDown, Plus, X } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { DashedButton } from '@/components/ui/dashed-button'
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { FormRow, FormSection, FormUnit } from '@/components/ui/form-section'
import { Input } from '@/components/ui/input'
import { OrderControls } from '@/components/ui/order-controls'
import { Select, SelectContent, SelectGroup, SelectItem, SelectLabel, SelectTrigger } from '@/components/ui/select'
import { StatusLight } from '@/components/ui/status-light'
import { DataPanel } from '@/components/ui/surface'
import { cn } from '@/lib/utils'
import type { FieldSchema } from '@/types/config-schema'
import '@/styles/task-board.css'

import { TASK_META, type TaskMeta } from '../taskMeta'
import type { ModelInfo, ModelTaskConfig, TaskConfig } from '../types'

const BASIC_TASKS = ['replyer', 'planner', 'utils', 'vlm']

const STRATEGIES = [
  { value: 'sequential', label: '按顺序', hint: '先用第 1 个，失败或超时再换下一个' },
  { value: 'balance', label: '轮流', hint: '按负载均衡分摊到每个模型' },
  { value: 'random', label: '随机', hint: '每次随机挑一个' },
]

const GUIDE_STORAGE_KEY = 'maibot-model-task-guide-dismissed'

function readGuideDismissed() {
  try {
    return localStorage.getItem(GUIDE_STORAGE_KEY) === 'true'
  } catch {
    return false
  }
}

type TaskField = keyof TaskConfig

interface TaskAssignmentBoardProps {
  fields: FieldSchema[]
  taskConfig: ModelTaskConfig
  models: ModelInfo[]
  webServices: string[]
  /** 当前在抽屉里编辑的工作，null 表示抽屉关闭 */
  openTask: string | null
  onOpenTaskChange: (task: string | null) => void
  onChange: (task: string, field: TaskField, value: string[] | number | string) => void
  onAddWebService: () => void
  /** 进入页面前就有一步没做完时显示的步骤完成情况 */
  hasProviders: boolean
  /** 打开逐步引导；不传时引导条里不显示入口 */
  onStartTour?: () => void
  /** 引导浮层位于抽屉外，允许操作引导且不触发外部点击关闭。 */
  tourRunning?: boolean
  /** 抽屉底部的附加内容，比如用量统计、嵌入建库进度 */
  renderExtra?: (task: string) => ReactNode
  fallbackLabel: (field: FieldSchema) => string
}

export function TaskAssignmentBoard({
  fields,
  taskConfig,
  models,
  webServices,
  openTask,
  onOpenTaskChange,
  onChange,
  onAddWebService,
  hasProviders,
  onStartTour,
  tourRunning = false,
  renderExtra,
  fallbackLabel,
}: TaskAssignmentBoardProps) {
  const [guideDismissed, setGuideDismissed] = useState(readGuideDismissed)
  const [advancedOpen, setAdvancedOpen] = useState(false)
  const taskFields = fields.filter((field) => field.type === 'object')
  const metaOf = (field: FieldSchema): TaskMeta =>
    TASK_META[field.name] ?? { name: fallbackLabel(field), desc: field.description ?? '' }
  const listOf = (name: string) => taskConfig[name]?.model_list ?? []

  const basicFields = BASIC_TASKS.flatMap((name) => taskFields.filter((field) => field.name === name))
  const advancedFields = taskFields.filter((field) => !BASIC_TASKS.includes(field.name))
  const advancedOn = advancedFields.filter((field) => listOf(field.name).length > 0).length
  // 搜索定位到进阶工作时自动展开
  const advancedVisible = advancedOpen || advancedFields.some((field) => field.name === openTask)

  const lampOf = (field: FieldSchema) =>
    listOf(field.name).length > 0 ? 'on' : metaOf(field).fallback ? 'fallback' : 'off'

  const setGuide = (dismissed: boolean) => {
    setGuideDismissed(dismissed)
    try {
      localStorage.setItem(GUIDE_STORAGE_KEY, String(dismissed))
    } catch {
      // 隐私模式下写不进去也不影响使用
    }
  }

  const strategyOf = (name: string) => taskConfig[name]?.selection_strategy || 'balance'
  const strategyLabel = (name: string) => STRATEGIES.find((item) => item.value === strategyOf(name))?.label ?? ''

  const editingField = taskFields.find((field) => field.name === openTask)

  return (
    <div data-task-board="true" className="flex min-h-0 flex-col gap-4">
      {!guideDismissed && (
        <DataPanel data-task-guide="true" padding="none" className="flex flex-wrap items-center gap-x-3 gap-y-2">
          <GuideStep index={1} done={hasProviders}>在模型库添加厂商</GuideStep>
          <span aria-hidden="true" className="text-muted-foreground">→</span>
          <GuideStep index={2} done={models.length > 0}>添加模型</GuideStep>
          <span aria-hidden="true" className="text-muted-foreground">→</span>
          <GuideStep index={3} done={false}>把模型安排到对应的工作</GuideStep>
          <span className="ml-auto flex items-center gap-3">
            {/* 逐步引导只在桌面端可用 */}
            {onStartTour && (
              <button type="button" data-task-board-link="true" className="hidden lg:inline" onClick={onStartTour}>
                跟着引导走一遍
              </button>
            )}
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              aria-label="关闭使用提示"
              onClick={() => setGuide(true)}
            >
              <X className="h-3.5 w-3.5" />
            </Button>
          </span>
        </DataPanel>
      )}

      <section aria-labelledby="task-board-basic">
        <div data-task-board-label="true">
          <b>BASIC</b>
          <h2 id="task-board-basic">基础</h2>
          {guideDismissed && (
            <button type="button" data-task-board-link="true" className="ml-auto" onClick={() => setGuide(false)}>
              使用提示
            </button>
          )}
        </div>
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          {basicFields.map((field) => {
            const meta = metaOf(field)
            const list = listOf(field.name)
            if (list.length === 0) {
              return (
                <div key={field.name} data-task-card="empty" data-config-field-path={`model_task_config.${field.name}`}>
                  <div className="flex items-center gap-2">
                    <TaskLamp state={lampOf(field)} />
                    <h3 className="text-base font-semibold">{meta.name}</h3>
                  </div>
                  <p className="text-xs text-muted-foreground">{meta.desc}</p>
                  <DashedButton className="mt-auto h-11" onClick={() => onOpenTaskChange(field.name)}>
                    + 安排模型
                  </DashedButton>
                </div>
              )
            }
            return (
              <DataPanel
                key={field.name}
                padding="none"
                data-task-card="set"
                data-config-field-path={`model_task_config.${field.name}`}
              >
                <div className="flex items-center gap-2">
                  <TaskLamp state="on" />
                  <h3 className="text-base font-semibold">{meta.name}</h3>
                </div>
                {meta.desc && <p className="text-xs text-muted-foreground">{meta.desc}</p>}
                <ol data-task-card-models="true">
                  {list.slice(0, 3).map((name, index) => (
                    <li key={name} title={name}>
                      {list.length > 1 && <span aria-hidden="true">{index + 1}</span>}
                      {name}
                    </li>
                  ))}
                  {list.length > 3 && <li data-task-card-more="true">+{list.length - 3}</li>}
                </ol>
                <div data-task-card-foot="true">
                  <span className="text-xs text-muted-foreground">
                    {list.length > 1 && !meta.hideStrategy ? strategyLabel(field.name) : ''}
                  </span>
                  <Button type="button" size="sm" variant="outline" onClick={() => onOpenTaskChange(field.name)}>
                    编辑
                  </Button>
                </div>
              </DataPanel>
            )
          })}
        </div>
      </section>

      {advancedFields.length > 0 && (
        <section>
          <DashedButton
            className="h-auto justify-start"
            data-task-board-more="true"
            aria-expanded={advancedVisible}
            aria-controls="task-board-advanced"
            onClick={() => setAdvancedOpen(!advancedVisible)}
          >
            <b>MORE</b>
            <span>进阶 · {advancedOn} / {advancedFields.length} 已开启</span>
            <ChevronDown className="ml-auto h-4 w-4" aria-hidden="true" data-task-board-chevron="true" />
          </DashedButton>
          {advancedVisible && (
            <DataPanel id="task-board-advanced" padding="none" data-task-board-list="true">
              {advancedFields.map((field) => {
                const meta = metaOf(field)
                const list = listOf(field.name)
                const empty = list.length === 0
                return (
                  <div
                    key={field.name}
                    data-task-row={empty ? 'empty' : 'set'}
                    data-config-field-path={`model_task_config.${field.name}`}
                  >
                    <TaskLamp state={lampOf(field)} />
                    <span data-task-row-name="true">{meta.name}</span>
                    <span data-task-row-value="true" title={list.join('、')}>
                      {empty ? meta.fallback ?? '未开启' : list.length > 1 ? `${list[0]} +${list.length - 1}` : list[0]}
                    </span>
                    {empty ? (
                      <DashedButton className="h-7" data-task-row-add="true" onClick={() => onOpenTaskChange(field.name)}>
                        + 开启
                      </DashedButton>
                    ) : (
                      <Button type="button" size="sm" variant="outline" onClick={() => onOpenTaskChange(field.name)}>
                        编辑
                      </Button>
                    )}
                  </div>
                )
              })}
            </DataPanel>
          )}
        </section>
      )}

      <Dialog open={Boolean(editingField)} modal={!tourRunning} onOpenChange={(open) => !open && onOpenTaskChange(null)}>
        {editingField && (
          <TaskEditor
            key={editingField.name}
            field={editingField}
            meta={metaOf(editingField)}
            config={taskConfig[editingField.name] ?? { model_list: [] }}
            candidates={
              editingField.name === 'web_search'
                ? webServices.map((name) => ({ name }))
                : candidatesFor(editingField.name, models)
            }
            onChange={(key, value) => onChange(editingField.name, key, value)}
            onAddWebService={onAddWebService}
            tourRunning={tourRunning}
            extra={renderExtra?.(editingField.name)}
          />
        )}
      </Dialog>
    </div>
  )
}

interface TaskCandidate {
  name: string
  /** 顺序列表里模型名下方的小字 */
  tags?: string
  provider?: string
  visual?: boolean
}

/** 下拉菜单按厂商分组，保持模型库里的先后顺序；联网服务没有厂商，不分组。 */
function groupByProvider(items: TaskCandidate[]) {
  const groups = new Map<string | undefined, TaskCandidate[]>()
  for (const item of items) {
    const group = groups.get(item.provider)
    if (group) group.push(item)
    else groups.set(item.provider, [item])
  }
  return Array.from(groups)
}

/** 只列出能胜任这项工作的模型：识图要能看图，嵌入只用嵌入模型。 */
function candidatesFor(task: string, models: ModelInfo[]) {
  const pick = (model: ModelInfo) => {
    if (task === 'embedding' || task === 'image_embedding') {
      return model.model_type === 'embedding' || model.model_type === 'image_embedding'
    }
    if (model.model_type !== 'llm') return false
    return task === 'vlm' ? Boolean(model.visual) : true
  }
  return models.filter(pick).map((model) => ({
    name: model.name,
    tags: [model.api_provider, model.visual ? '视觉' : ''].filter(Boolean).join(' · '),
    provider: model.api_provider,
    visual: Boolean(model.visual),
  }))
}

function GuideStep({ index, done, children }: { index: number; done: boolean; children: ReactNode }) {
  return (
    <span data-task-guide-step={done ? 'done' : 'todo'} className="flex items-center gap-2 text-sm">
      <i aria-hidden="true">{index}</i>
      {children}
    </span>
  )
}

/** 模型库里一行模型「负责的工作」：亮灯加工作名，没有工作时灰灯「未使用」。 */
export function ModelTaskSummary({ used, tasks, className }: { used: boolean; tasks: string[]; className?: string }) {
  const text = tasks.length > 0 ? tasks.join('、') : used ? '已使用' : '未使用'
  return (
    <span
      data-model-task-summary={used ? 'used' : 'unused'}
      className={cn('flex min-w-0 items-center gap-1.5 text-xs', !used && 'text-muted-foreground', className)}
      title={text}
    >
      <TaskLamp state={used ? 'on' : 'off'} label={used ? '已使用' : '未使用'} />
      <span className="truncate">{text}</span>
    </span>
  )
}

/** 已安排亮绿灯；留空但会自动回退为黄灯；留空即关闭为灰灯。 */
function TaskLamp({ state, label: labelOverride }: { state: 'on' | 'fallback' | 'off'; label?: string }) {
  const label = labelOverride ?? (state === 'on' ? '已安排' : state === 'fallback' ? '留空，自动回退' : '未安排')
  return (
    <StatusLight
      status={state === 'on' ? 'on' : state === 'fallback' ? 'mixed' : 'off'}
      glow={state === 'on'}
      label={label}
    />
  )
}

function TaskEditor({
  field,
  meta,
  config,
  candidates,
  onChange,
  onAddWebService,
  tourRunning,
  extra,
}: {
  field: FieldSchema
  meta: TaskMeta
  config: TaskConfig
  candidates: TaskCandidate[]
  onChange: (key: TaskField, value: string[] | number | string) => void
  onAddWebService: () => void
  tourRunning: boolean
  extra?: ReactNode
}) {
  const list = config.model_list
  const isWeb = field.name === 'web_search'
  const single = Boolean(meta.singleModel)
  const addable = candidates.filter((candidate) => !list.includes(candidate.name))
  const tagsOf = (name: string) => candidates.find((candidate) => candidate.name === name)?.tags
  const strategy = config.selection_strategy || 'balance'
  const move = (from: number, to: number) => {
    const next = list.slice()
    const [item] = next.splice(from, 1)
    next.splice(to, 0, item)
    onChange('model_list', next)
  }
  const [dragIndex, setDragIndex] = useState<number | null>(null)

  return (
    <DialogContent
      side="right"
      className="bottom-4 flex max-h-none flex-col gap-0 p-0 pb-0 sm:[--dialog-width:30rem]"
      {...(meta.desc ? {} : { 'aria-describedby': undefined })}
      data-task-editor="true"
      preventOutsideClose={tourRunning}
    >
      <DialogHeader data-task-editor-head="true" className="px-5 pt-4 pb-3">
        <DialogTitle className="flex items-center gap-2">
          <TaskLamp state={list.length > 0 ? 'on' : meta.fallback ? 'fallback' : 'off'} />
          {meta.name}
        </DialogTitle>
        {meta.desc && <DialogDescription className="text-xs">{meta.desc}</DialogDescription>}
      </DialogHeader>
      <DialogBody type="auto" data-form-scroll="true" viewportClassName="pr-0">
        <div className="flex flex-col gap-5 px-5 py-4" data-config-field-path={`model_task_config.${field.name}`}>
          <FormSection
            title={isWeb ? '联网服务' : '模型'}
            action={
              <span className="flex items-center gap-3 text-xs text-muted-foreground">
                {single ? '只能选一个' : `${list.length} 个`}
                {list.length > 0 && !meta.required && (
                  <button
                    type="button"
                    data-task-board-link="danger"
                    onClick={() => onChange('model_list', [])}
                  >
                    清空
                  </button>
                )}
              </span>
            }
          >
            <DataPanel padding="none" data-task-order-panel="true" data-tour="task-model-select">
            <ol data-task-order="true" aria-label={`${meta.name}的模型顺序`}>
              {list.map((name, index) => (
                <li
                  key={name}
                  draggable={!single}
                  data-dragging={dragIndex === index ? 'true' : undefined}
                  onDragStart={() => setDragIndex(index)}
                  onDragOver={(event) => {
                    event.preventDefault()
                    if (dragIndex !== null && dragIndex !== index) {
                      move(dragIndex, index)
                      setDragIndex(index)
                    }
                  }}
                  onDragEnd={() => setDragIndex(null)}
                >
                  {!single && <span data-task-order-grip="true" aria-hidden="true" />}
                  {!single && <span data-task-order-no="true">{index + 1}</span>}
                  <span className="min-w-0 flex-1">
                    <span className="block truncate font-mono text-sm" title={name}>{name}</span>
                    {tagsOf(name) && <span className="block truncate text-xs text-muted-foreground">{tagsOf(name)}</span>}
                    {!candidates.some((candidate) => candidate.name === name) && (
                      <span className="block text-xs text-destructive">模型库里找不到这个模型</span>
                    )}
                  </span>
                  {!single && (
                    <OrderControls
                      label={name}
                      index={index}
                      count={list.length}
                      upLabel="上移"
                      downLabel="下移"
                      onMove={(delta) => move(index, index + delta)}
                    />
                  )}
                  <Button
                    type="button" variant="ghost" size="icon-sm" aria-label={`移除 ${name}`}
                    className="text-destructive hover:text-destructive"
                    onClick={() => onChange('model_list', list.filter((item) => item !== name))}
                  >
                    <X className="h-3.5 w-3.5" />
                  </Button>
                </li>
              ))}
            </ol>
            {list.length === 0 && (
              <p className="px-3 py-3 text-xs text-muted-foreground">
                {meta.required
                  ? '这是麦麦必须的工作，至少安排一个。'
                  : meta.fallback ? `留空时自动${meta.fallback}。` : '留空时这项能力关闭。'}
              </p>
            )}
            </DataPanel>
            <div data-task-order-add="true" className="flex gap-2">
              {addable.length > 0 ? (
                <Select
                  value=""
                  onValueChange={(name) => onChange('model_list', single ? [name] : [...list, name])}
                >
                  {/* 触发器里的 span 会被截成单行，图标和文字直接放在触发器里，避免加号被挤到上一行 */}
                  <SelectTrigger
                    aria-label="添加模型"
                    className="min-w-0 flex-1 justify-start gap-1.5 text-muted-foreground [&>svg:last-child]:ml-auto"
                  >
                    <Plus className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                    {single && list.length > 0 ? '换成…' : isWeb ? '选择联网服务' : '添加模型'}
                  </SelectTrigger>
                  <SelectContent>
                    {/* 按厂商分组，同名模型也能分清来自哪家 */}
                    {groupByProvider(addable).map(([provider, items]) => (
                      <SelectGroup key={provider ?? ''}>
                        {provider && <SelectLabel>{provider}</SelectLabel>}
                        {items.map((candidate) => (
                          <SelectItem key={candidate.name} value={candidate.name}>
                            {candidate.name}
                            {candidate.visual ? <span className="ml-2 text-xs text-muted-foreground">视觉</span> : null}
                          </SelectItem>
                        ))}
                      </SelectGroup>
                    ))}
                  </SelectContent>
                </Select>
              ) : (
                <p className="flex-1 text-xs text-muted-foreground">
                  {candidates.length === 0
                    ? field.name === 'vlm' ? '模型库里还没有能看图的模型' : '模型库里还没有可用的模型'
                    : '可用的都已添加'}
                </p>
              )}
              {isWeb && (
                <Button type="button" variant="outline" onClick={onAddWebService}>添加联网服务</Button>
              )}
            </div>
          </FormSection>

          {!isWeb && !meta.hideStrategy && list.length > 1 && (
            <FormSection title="使用方式">
              <DataPanel padding="none" role="group" aria-label="使用方式" data-task-strategy="true">
                {STRATEGIES.map((item) => (
                  <Button
                    key={item.value}
                    type="button"
                    size="sm"
                    variant={strategy === item.value ? 'default' : 'outline'}
                    aria-pressed={strategy === item.value}
                    onClick={() => onChange('selection_strategy', item.value)}
                  >
                    {item.label}
                  </Button>
                ))}
              </DataPanel>
              <p className="pt-2 text-xs text-muted-foreground">
                {STRATEGIES.find((item) => item.value === strategy)?.hint}
              </p>
            </FormSection>
          )}

          {!isWeb && (
            <FormSection title="参数">
              {!meta.hideTemperature && (
                <FormRow label="温度" htmlFor={`task-${field.name}-temperature`}>
                  <Input
                    id={`task-${field.name}-temperature`}
                    type="number" min={0} max={2} step={0.1} className="w-28"
                    value={config.temperature ?? 0.7}
                    onChange={(event) => {
                      const value = parseFloat(event.target.value)
                      if (!Number.isNaN(value)) onChange('temperature', Math.min(2, Math.max(0, value)))
                    }}
                  />
                </FormRow>
              )}
              {!meta.hideMaxTokens && (
                <FormRow label="最大 Token" htmlFor={`task-${field.name}-max-tokens`}>
                  <div className="flex items-center gap-2">
                    <Input
                      id={`task-${field.name}-max-tokens`}
                      type="number" min={1} className="w-28"
                      value={config.max_tokens ?? 4096}
                      onChange={(event) => {
                        const value = parseInt(event.target.value, 10)
                        if (!Number.isNaN(value)) onChange('max_tokens', value)
                      }}
                    />
                    <FormUnit>token</FormUnit>
                  </div>
                </FormRow>
              )}
              <FormRow label="超时" htmlFor={`task-${field.name}-timeout`}>
                <div className="flex items-center gap-2">
                  <Input
                    id={`task-${field.name}-timeout`}
                    type="number" min={1} step={1} className="w-28" placeholder="240"
                    value={config.hard_timeout ?? ''}
                    onChange={(event) => {
                      const value = parseInt(event.target.value, 10)
                      if (!Number.isNaN(value) && value >= 1) onChange('hard_timeout', value)
                    }}
                  />
                  <FormUnit>秒</FormUnit>
                </div>
              </FormRow>
            </FormSection>
          )}

          {extra}
        </div>
      </DialogBody>
    </DialogContent>
  )
}
