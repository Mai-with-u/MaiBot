/* eslint-disable react-refresh/only-export-components -- Replay 编辑组件需与其 Item 编辑工厂共同导出。 */

import { useEffect, useState } from 'react'
import { Braces, FileText, Loader2, Play, Plus, Trash2, X } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { CodeEditor } from '@/components/CodeEditor'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { ScrollArea } from '@/components/ui/scroll-area'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { useToast } from '@/hooks/use-toast'
import { getModelConfig } from '@/lib/config-api'
import {
  replayReasoningPrompt,
  type ContextItemSnapshot,
  type ReasoningPromptFile,
  type ReasoningReplayResponse,
} from '@/lib/reasoning-process-api'
import { cn } from '@/lib/utils'
import { ContextItemTimeline } from './context-items'
import { GenerationAttemptTimeline } from './generation-attempts'
import { isRecord, normalizeContextItemSnapshot, type StructuredPromptPayload } from './schema'

const REPLAY_COUNT_MAX = 20
function formatDurationMs(durationMs: number | null): string {
  if (durationMs === null || !Number.isFinite(durationMs)) return ''
  if (durationMs < 1000) return `${durationMs.toFixed(durationMs >= 100 ? 0 : 1)} ms`
  return `${(durationMs / 1000).toFixed(2)} s`
}

export type EditableReplayItem = {
  id: string
  itemType: string
  jsonText: string
}

export type ReplayRunResult = {
  id: string
  index: number
  result: ReasoningReplayResponse | null
  error: string | null
}

export type ReplayModelOption = {
  name: string
}

export function unwrapModelConfigPayload(payload: unknown): Record<string, unknown> {
  if (!payload || typeof payload !== 'object') {
    return {}
  }

  const record = payload as Record<string, unknown>
  const config = record.config
  return config && typeof config === 'object' ? (config as Record<string, unknown>) : record
}

export function normalizeReplayModelOptions(payload: unknown): ReplayModelOption[] {
  const config = unwrapModelConfigPayload(payload)
  const rawModels = config.models
  if (!Array.isArray(rawModels)) {
    return []
  }

  return rawModels
    .map((item) => {
      if (!item || typeof item !== 'object') {
        return null
      }

      const record = item as Record<string, unknown>
      const name = String(record.name ?? '').trim()
      if (!name) {
        return null
      }

      return {
        name,
      }
    })
    .filter((item): item is ReplayModelOption => item !== null)
}

export function createEditableReplayItems(
  prompt: StructuredPromptPayload | null
): EditableReplayItem[] {
  return (prompt?.request_items ?? []).map((item, index) => {
    return {
      id: `${item.meta.item_id}-${index}`,
      itemType: item.item_type,
      jsonText: JSON.stringify(item, null, 2),
    }
  })
}

export function createBlankReplayItem(): EditableReplayItem {
  const itemId =
    typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
      ? crypto.randomUUID().replaceAll('-', '')
      : `manual${Date.now()}${Math.random().toString(36).slice(2)}`
  const item: ContextItemSnapshot = {
    item_type: 'UserMessageItem',
    meta: {
      item_id: itemId,
      logical_turn_id: null,
      timestamp: new Date().toISOString(),
    },
    parts: [{ type: 'text', text: '' }],
  }
  return {
    id: itemId,
    itemType: item.item_type,
    jsonText: JSON.stringify(item, null, 2),
  }
}

export function parseEditableReplayItems(items: EditableReplayItem[]): ContextItemSnapshot[] {
  return items.map((item, index) => {
    let parsed: unknown
    try {
      parsed = JSON.parse(item.jsonText) as unknown
    } catch {
      throw new Error(`第 ${index + 1} 个 Item 不是有效 JSON`)
    }
    const normalized = normalizeContextItemSnapshot(parsed, index, 'replay')
    if (!normalized || !normalized.meta.item_id) {
      throw new Error(`第 ${index + 1} 个 Item 缺少 item_type 或 meta.item_id`)
    }
    return normalized
  })
}

type ReplayTextPart = {
  index: number
  text: string
}

export function getReplayTextParts(jsonText: string): ReplayTextPart[] {
  try {
    const parsed = JSON.parse(jsonText) as unknown
    if (!isRecord(parsed) || !Array.isArray(parsed.parts)) return []
    return parsed.parts.flatMap((part, index) =>
      isRecord(part) && part.type === 'text' && typeof part.text === 'string'
        ? [{ index, text: part.text }]
        : []
    )
  } catch {
    return []
  }
}

export function updateReplayTextPart(jsonText: string, partIndex: number, text: string): string {
  const parsed = JSON.parse(jsonText) as unknown
  if (!isRecord(parsed) || !Array.isArray(parsed.parts) || !isRecord(parsed.parts[partIndex])) {
    throw new Error('无法更新正文：Item 的 parts 结构无效')
  }
  const parts = parsed.parts.map((part, index) =>
    index === partIndex && isRecord(part) ? { ...part, text } : part
  )
  return JSON.stringify({ ...parsed, parts }, null, 2)
}

function ReplayItemBodyEditor({
  item,
  updateItem,
}: {
  item: EditableReplayItem
  updateItem: (id: string, patch: Partial<EditableReplayItem>) => void
}) {
  const [showJson, setShowJson] = useState(false)
  const textParts = getReplayTextParts(item.jsonText)
  const hasReadableText = textParts.length > 0

  const updateJsonText = (jsonText: string) => {
    const itemTypeMatch = jsonText.match(/"item_type"\s*:\s*"([^"]+)"/)
    updateItem(item.id, {
      jsonText,
      ...(itemTypeMatch ? { itemType: itemTypeMatch[1] } : {}),
    })
  }

  return (
    <div className="space-y-3">
      {hasReadableText && !showJson ? (
        <div className="space-y-3">
          {textParts.map((part, textIndex) => (
            <div key={part.index} className="space-y-1.5">
              <Label className="text-muted-foreground text-xs">
                {textParts.length > 1 ? `正文 ${textIndex + 1}` : '正文'}
              </Label>
              <Textarea
                value={part.text}
                onChange={(event) => {
                  updateJsonText(
                    updateReplayTextPart(item.jsonText, part.index, event.target.value)
                  )
                }}
                minHeight={180}
                maxHeight={560}
                className="text-sm leading-6"
              />
            </div>
          ))}
        </div>
      ) : (
        <CodeEditor
          value={item.jsonText}
          onChange={updateJsonText}
          language="json"
          height="clamp(220px, 42vh, 560px)"
          minHeight="220px"
          maxHeight="560px"
          className="text-xs"
        />
      )}

      <div className="flex flex-wrap items-center gap-2">
        {hasReadableText && (
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="h-8 gap-1.5"
            onClick={() => setShowJson((current) => !current)}
          >
            {showJson ? <FileText className="h-3.5 w-3.5" /> : <Braces className="h-3.5 w-3.5" />}
            {showJson ? '正文编辑' : 'JSON 编辑'}
          </Button>
        )}
      </div>
    </div>
  )
}

export function formatReplayTokenSummary(result: ReasoningReplayResponse): string {
  const parts = [
    `输入 ${result.prompt_tokens}`,
    `输出 ${result.completion_tokens}`,
    `总计 ${result.total_tokens}`,
  ]
  if (result.prompt_cache_hit_tokens > 0 || result.prompt_cache_miss_tokens > 0) {
    parts.push(`缓存命中 ${result.prompt_cache_hit_tokens}`)
  }
  if (result.duration_ms > 0) {
    parts.push(`耗时 ${formatDurationMs(result.duration_ms)}`)
  }
  return parts.join(' · ')
}

export function ReplayItemEditorColumn({
  selectedTitle,
  items,
  updateItem,
  addItem,
  deleteItem,
  onClose,
}: {
  selectedTitle: string
  items: EditableReplayItem[]
  updateItem: (id: string, patch: Partial<EditableReplayItem>) => void
  addItem: () => void
  deleteItem: (id: string) => void
  onClose: () => void
}) {
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex min-h-12 flex-shrink-0 items-center justify-between gap-3 border-b px-3 py-2 sm:min-h-14 sm:px-4 sm:py-3">
        <div className="min-w-0">
          <div className="flex min-w-0 items-center gap-2">
            <span className="truncate text-sm font-medium">编辑重放 Items</span>
            <Badge variant="secondary">{items.length} 个</Badge>
          </div>
          <div className="text-muted-foreground mt-1 truncate text-xs">{selectedTitle}</div>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <Button variant="outline" size="sm" className="h-8 gap-1.5" onClick={addItem}>
            <Plus className="h-4 w-4" />
            添加 Item
          </Button>
          <Button
            variant="outline"
            size="sm"
            className="h-8 w-8 p-0"
            onClick={onClose}
            title="退出重放编辑"
            aria-label="退出重放编辑"
          >
            <X className="h-4 w-4" />
          </Button>
        </div>
      </div>

      <ScrollArea className="min-h-0 flex-1">
        <div className="divide-y">
          {items.length === 0 ? (
            <div className="text-muted-foreground px-3 py-10 text-center text-sm">
              这条记录没有可重放的 request Items。
            </div>
          ) : (
            items.map((item, index) => (
              <section key={item.id} className="p-3 sm:p-4">
                <div className="mb-2 flex flex-wrap items-center gap-2">
                  <Badge variant="outline">#{index + 1}</Badge>
                  <Badge variant="secondary" className="font-mono">
                    {item.itemType}
                  </Badge>
                  <Button
                    variant="outline"
                    size="sm"
                    className="ml-auto h-8 w-8 p-0"
                    onClick={() => deleteItem(item.id)}
                    title="删除 Item"
                    aria-label={`删除第 ${index + 1} 个 Item`}
                  >
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </div>
                <ReplayItemBodyEditor item={item} updateItem={updateItem} />
              </section>
            ))
          )}
        </div>
      </ScrollArea>
    </div>
  )
}

export function ReplayResultItem({ item }: { item: ReplayRunResult }) {
  const result = item.result

  if (!result) {
    return (
      <div className="space-y-2 rounded-md border p-3">
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant="destructive">#{item.index} 失败</Badge>
        </div>
        <div className="border-destructive/30 bg-destructive/10 text-destructive rounded-md border px-3 py-2 text-sm">
          {item.error || '请求重放接口失败'}
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-3 rounded-md border p-3">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant={result.success ? 'default' : 'destructive'}>
          #{item.index} {result.success ? '完成' : '失败'}
        </Badge>
        <span className="text-muted-foreground text-xs">{result.model_name}</span>
      </div>
      <div className="text-muted-foreground text-xs leading-5">
        {formatReplayTokenSummary(result)}
      </div>
      {result.error && (
        <div className="border-destructive/30 bg-destructive/10 text-destructive rounded-md border px-3 py-2 text-sm">
          {result.error}
        </div>
      )}
      <GenerationAttemptTimeline attempts={result.generation_attempts} />
      <ContextItemTimeline
        title="重放输出"
        items={result.output_items}
        avatarMap={{}}
        botSelfNames={new Set<string>()}
      />
    </div>
  )
}

export function ReasoningReplayPanel({
  open,
  onClose,
  selected,
  selectedTitle,
  structuredPrompt,
  items,
}: {
  open: boolean
  onClose: () => void
  selected: ReasoningPromptFile | null
  selectedTitle: string
  structuredPrompt: StructuredPromptPayload | null
  items: EditableReplayItem[]
}) {
  const { toast } = useToast()
  const [modelName, setModelName] = useState('')
  const [modelOptions, setModelOptions] = useState<ReplayModelOption[]>([])
  const [loadingModels, setLoadingModels] = useState(false)
  const [modelLoadError, setModelLoadError] = useState<string | null>(null)
  const [temperature, setTemperature] = useState('')
  const [maxTokens, setMaxTokens] = useState('')
  const [replayCount, setReplayCount] = useState('1')
  const [replayResults, setReplayResults] = useState<ReplayRunResult[]>([])
  const [submitting, setSubmitting] = useState(false)
  const [runningReplayIndex, setRunningReplayIndex] = useState(0)

  useEffect(() => {
    if (!open) {
      return
    }

    const snapshotModelName = structuredPrompt?.metadata?.model_name || selected?.model_name || ''
    setModelName(snapshotModelName)
    setTemperature('')
    setMaxTokens('')
    setReplayCount('1')
    setReplayResults([])
    setRunningReplayIndex(0)
  }, [open, selected, structuredPrompt])

  useEffect(() => {
    if (!open) {
      return
    }

    let cancelled = false
    const snapshotModelName = (
      structuredPrompt?.metadata?.model_name ||
      selected?.model_name ||
      ''
    ).trim()
    setLoadingModels(true)
    setModelLoadError(null)

    getModelConfig()
      .then((payload) => {
        if (cancelled) {
          return
        }

        const nextModelOptions = normalizeReplayModelOptions(payload)
        setModelOptions(nextModelOptions)
        if (nextModelOptions.length === 0) {
          setModelName('')
          return
        }

        const modelNames = new Set(nextModelOptions.map((model) => model.name))
        setModelName(
          modelNames.has(snapshotModelName) ? snapshotModelName : nextModelOptions[0].name
        )
      })
      .catch((error: unknown) => {
        if (cancelled) {
          return
        }

        const message = error instanceof Error ? error.message : '读取模型配置失败'
        setModelOptions([])
        setModelName('')
        setModelLoadError(message)
        toast({
          title: '加载模型列表失败',
          description: message,
          variant: 'destructive',
        })
      })
      .finally(() => {
        if (!cancelled) {
          setLoadingModels(false)
        }
      })

    return () => {
      cancelled = true
    }
  }, [open, selected?.model_name, structuredPrompt?.metadata?.model_name, toast])

  const handleReplay = async () => {
    const normalizedModelName = modelName.trim()
    if (!normalizedModelName) {
      toast({
        title: '缺少模型名称',
        description: '请选择 model_config.toml 中已配置的模型名称。',
        variant: 'destructive',
      })
      return
    }
    const normalizedReplayCount = Number(replayCount.trim())
    if (
      !Number.isInteger(normalizedReplayCount) ||
      normalizedReplayCount < 1 ||
      normalizedReplayCount > REPLAY_COUNT_MAX
    ) {
      toast({
        title: '重放次数无效',
        description: `请输入 1-${REPLAY_COUNT_MAX} 之间的整数。`,
        variant: 'destructive',
      })
      return
    }
    if (items.length === 0) {
      toast({
        title: '没有可重放的 Items',
        description: '这条记录没有结构化 request_items。',
        variant: 'destructive',
      })
      return
    }

    let requestItems: ContextItemSnapshot[]
    try {
      requestItems = parseEditableReplayItems(items)
    } catch (err) {
      toast({
        title: 'Item JSON 无效',
        description: err instanceof Error ? err.message : '请检查 Item JSON。',
        variant: 'destructive',
      })
      return
    }
    const toolDefinitions = (structuredPrompt?.tool_definitions ?? []).filter(isRecord)

    setSubmitting(true)
    setReplayResults([])
    setRunningReplayIndex(0)
    let successCount = 0
    try {
      for (let index = 1; index <= normalizedReplayCount; index += 1) {
        setRunningReplayIndex(index)
        try {
          const replayResult = await replayReasoningPrompt({
            source_path: selected?.json_path ?? null,
            stage: selected?.stage ?? structuredPrompt?.request?.kind ?? '',
            model_name: normalizedModelName,
            item_schema_version: 1,
            request_items: requestItems,
            tool_definitions: toolDefinitions,
            temperature: temperature.trim() ? Number(temperature) : null,
            max_tokens: maxTokens.trim() ? Number(maxTokens) : null,
          })
          if (replayResult.success) {
            successCount += 1
          }
          setReplayResults((current) => [
            ...current,
            { id: `${Date.now()}-${index}`, index, result: replayResult, error: null },
          ])
        } catch (err) {
          setReplayResults((current) => [
            ...current,
            {
              id: `${Date.now()}-${index}`,
              index,
              result: null,
              error: err instanceof Error ? err.message : '请求重放接口失败',
            },
          ])
        }
      }
      toast({
        title: '批量重放完成',
        description: `成功 ${successCount}/${normalizedReplayCount} 次。`,
        variant: successCount === normalizedReplayCount ? 'default' : 'destructive',
      })
    } finally {
      setRunningReplayIndex(0)
      setSubmitting(false)
    }
  }

  return (
    <aside
      className={cn(
        'bg-background min-h-0 flex-col overflow-hidden rounded-md border shadow-sm',
        open ? 'flex' : 'hidden'
      )}
      aria-hidden={!open}
    >
      <div className="flex min-h-14 items-center justify-between gap-3 border-b px-3 py-2 sm:px-4">
        <div className="min-w-0">
          <div className="text-sm font-semibold">重放推理请求</div>
          <div className="text-muted-foreground truncate text-xs">{selectedTitle}</div>
        </div>
        <Button
          variant="outline"
          size="sm"
          className="h-8 w-8 p-0"
          onClick={onClose}
          disabled={submitting}
          title="关闭重放边栏"
          aria-label="关闭重放边栏"
        >
          <X className="h-4 w-4" />
        </Button>
      </div>

      <ScrollArea className="min-h-0 flex-1">
        <div className="divide-y">
          <section className="p-3 sm:p-4">
            <div className="grid gap-3">
              <div className="grid gap-2">
                <Label htmlFor="reasoning-replay-model">模型名称</Label>
                <Select
                  value={modelName}
                  onValueChange={setModelName}
                  disabled={loadingModels || submitting || modelOptions.length === 0}
                >
                  <SelectTrigger id="reasoning-replay-model">
                    <SelectValue
                      placeholder={
                        loadingModels
                          ? '加载模型中...'
                          : modelLoadError
                            ? '模型列表加载失败'
                            : '选择模型'
                      }
                    />
                  </SelectTrigger>
                  <SelectContent>
                    {modelOptions.map((model) => (
                      <SelectItem key={model.name} value={model.name}>
                        {model.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                {!loadingModels && modelOptions.length === 0 ? (
                  <div className="text-destructive text-xs">
                    {modelLoadError || 'model_config.toml 中没有可选模型。'}
                  </div>
                ) : null}
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div className="grid gap-2">
                  <Label htmlFor="reasoning-replay-temperature">温度</Label>
                  <Input
                    id="reasoning-replay-temperature"
                    type="number"
                    min={0}
                    max={2}
                    step={0.1}
                    value={temperature}
                    onChange={(event) => setTemperature(event.target.value)}
                    placeholder="默认"
                  />
                </div>
                <div className="grid gap-2">
                  <Label htmlFor="reasoning-replay-max-tokens">最大 Token</Label>
                  <Input
                    id="reasoning-replay-max-tokens"
                    type="number"
                    min={1}
                    step={1}
                    value={maxTokens}
                    onChange={(event) => setMaxTokens(event.target.value)}
                    placeholder="默认"
                  />
                </div>
              </div>
              <div className="grid grid-cols-[minmax(0,1fr)_5.5rem] items-end gap-3">
                <Button
                  className="h-9 w-full gap-1.5"
                  onClick={handleReplay}
                  disabled={
                    submitting || loadingModels || modelOptions.length === 0 || items.length === 0
                  }
                >
                  {submitting ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <Play className="h-4 w-4" />
                  )}
                  {submitting && runningReplayIndex > 0
                    ? `执行中 ${runningReplayIndex}/${replayCount.trim() || '?'}`
                    : '执行重放'}
                </Button>
                <div className="grid gap-2">
                  <Label htmlFor="reasoning-replay-count">次数</Label>
                  <Input
                    id="reasoning-replay-count"
                    type="number"
                    min={1}
                    max={REPLAY_COUNT_MAX}
                    step={1}
                    value={replayCount}
                    onChange={(event) => setReplayCount(event.target.value)}
                  />
                </div>
              </div>
            </div>
          </section>

          <section className="space-y-3 p-3 sm:p-4">
            <div className="flex items-center justify-between gap-2">
              <div className="text-sm font-semibold">重放结果</div>
              {submitting && (
                <span className="text-muted-foreground inline-flex items-center gap-1.5 text-xs">
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />第 {runningReplayIndex || 1} 次
                </span>
              )}
            </div>
            {replayResults.length === 0 && !submitting ? (
              <div className="text-muted-foreground rounded-md border border-dashed px-3 py-4 text-sm">
                执行重放后，模型返回的完整 output Items 会显示在这里。
              </div>
            ) : null}
            {replayResults.length > 0 && (
              <div className="space-y-3">
                {replayResults.map((item) => (
                  <ReplayResultItem key={item.id} item={item} />
                ))}
              </div>
            )}
          </section>
        </div>
      </ScrollArea>
    </aside>
  )
}
