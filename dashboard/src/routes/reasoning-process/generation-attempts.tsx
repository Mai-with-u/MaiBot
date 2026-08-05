/* eslint-disable react-refresh/only-export-components -- Attempt 组件与 Provider 展示辅助共享同一协议模块。 */

import { AlertTriangle, CheckCircle2, ChevronDown, Code2 } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import type {
  GenerationAttemptSnapshot,
  GenerationTraceSnapshot,
} from '@/lib/reasoning-process-api'
import { isRecord, stringifyStructuredValue } from './schema'
import { ContextItemTimeline, ToolDefinitionsCollapsible } from './context-items'

type ProviderResponsePayload = Record<string, unknown> & {
  output?: unknown[]
}

function formatDurationMs(durationMs: number | null): string {
  if (durationMs === null || !Number.isFinite(durationMs)) return ''
  if (durationMs < 1000) return `${durationMs.toFixed(durationMs >= 100 ? 0 : 1)} ms`
  return `${(durationMs / 1000).toFixed(2)} s`
}
function formatSnapshotTime(value?: string): string {
  if (!value) return ''
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN')
}
function formatLlmRequestStatus(status?: string): string {
  const labels: Record<string, string> = {
    failed: '本次失败',
    final_failed: '最终失败',
    retrying: '等待重试',
    switching_model: '切换模型',
    succeeded: '请求成功',
    completed: '请求成功',
    succeeded_after_retry: '重试后成功',
  }
  return labels[status ?? ''] ?? status ?? '状态未知'
}
function getLlmRequestStatusStyle(status?: string): string {
  if (status === 'succeeded' || status === 'completed' || status === 'succeeded_after_retry')
    return 'border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300'
  if (status === 'retrying' || status === 'switching_model')
    return 'border-amber-500/30 bg-amber-500/10 text-amber-700 dark:bg-amber-950 dark:text-amber-300'
  return 'border-destructive/30 bg-destructive/10 text-destructive'
}

export function getProviderItemTypeLabel(itemType: string): string {
  const labels: Record<string, string> = {
    reasoning: '推理',
    message: '消息输出',
    web_search_call: '联网搜索',
    function_call: 'Function 调用',
    function_call_output: 'Function 结果',
    file_search_call: '文件搜索',
    code_interpreter_call: '代码解释器',
    image_generation_call: '图像生成',
    mcp_call: 'MCP 调用',
    mcp_list_tools: 'MCP 工具列表',
    computer_call: '计算机操作',
    shell_call: 'Shell 调用',
    apply_patch_call: '补丁调用',
    custom_tool_call: '自定义工具调用',
  }
  return labels[itemType] || itemType || '未知 Item'
}

export function extractProviderTextParts(value: unknown): string[] {
  if (typeof value === 'string') return value.trim() ? [value] : []
  if (!Array.isArray(value)) {
    if (!isRecord(value)) return []
    const textParts: string[] = []
    for (const key of ['text', 'refusal']) {
      const text = value[key]
      if (typeof text === 'string' && text.trim()) textParts.push(text)
    }
    for (const key of ['content', 'summary']) {
      textParts.push(...extractProviderTextParts(value[key]))
    }
    return textParts
  }
  return value.flatMap(extractProviderTextParts)
}

export function parseProviderArguments(value: unknown): unknown {
  if (typeof value !== 'string') return value
  const normalizedValue = value.trim()
  if (!normalizedValue) return ''
  try {
    return JSON.parse(normalizedValue) as unknown
  } catch {
    return value
  }
}

export function getProviderItemReadableText(
  item: Record<string, unknown>,
  itemType: string
): string {
  if (itemType === 'reasoning') {
    return [...extractProviderTextParts(item.summary), ...extractProviderTextParts(item.content)]
      .join('\n\n')
      .trim()
  }
  if (itemType === 'message') {
    return extractProviderTextParts(item.content).join('\n\n').trim()
  }
  return ''
}

export function getProviderItemPayload(item: Record<string, unknown>, itemType: string): unknown {
  if (itemType === 'function_call' || itemType === 'custom_tool_call') {
    return {
      ...(item.name ? { name: item.name } : {}),
      arguments: parseProviderArguments(item.arguments),
    }
  }
  if (item.action !== undefined) return item.action
  if (itemType !== 'message' && itemType !== 'reasoning' && item.output !== undefined) {
    return item.output
  }
  return null
}

export function GenerationTraceCard({ trace }: { trace: GenerationTraceSnapshot }) {
  return (
    <section className="space-y-2 rounded-md border border-violet-500/30 bg-violet-500/[0.03] p-2.5 sm:p-3">
      <div className="flex flex-wrap items-center gap-2">
        <Code2 className="h-4 w-4 text-violet-600 dark:text-violet-400" />
        <span className="text-sm font-semibold">Generation Trace</span>
        <Badge variant="outline">{trace.status || 'unknown'}</Badge>
        <Badge variant="secondary">{trace.output_item_ids.length} Items</Badge>
        {trace.response_id && (
          <span className="text-muted-foreground ml-auto font-mono text-[11px]">
            {trace.response_id}
          </span>
        )}
      </div>
      <div className="text-muted-foreground grid gap-1 text-xs sm:grid-cols-2">
        <span>Provider：{trace.provider || '未知'}</span>
        <span>模型：{trace.model || '未知'}</span>
        <span className="break-all sm:col-span-2">Endpoint：{trace.endpoint || '未知'}</span>
        <span>
          Token：{trace.prompt_tokens} + {trace.completion_tokens} = {trace.total_tokens}
        </span>
        <span>
          缓存：命中 {trace.prompt_cache_hit_tokens} / 未命中 {trace.prompt_cache_miss_tokens}
        </span>
      </div>
      {trace.output_item_ids.length > 0 && (
        <div className="text-muted-foreground font-mono text-[10px] break-all">
          output_item_ids: {trace.output_item_ids.join(', ')}
        </div>
      )}
    </section>
  )
}

export function ProviderResponseTimeline({ response }: { response: ProviderResponsePayload }) {
  const outputItems: unknown[] = Array.isArray(response.output) ? response.output : []
  const usage = isRecord(response.usage) ? response.usage : null
  const inputTokens = usage && typeof usage.input_tokens === 'number' ? usage.input_tokens : null
  const outputTokens = usage && typeof usage.output_tokens === 'number' ? usage.output_tokens : null
  const totalTokens = usage && typeof usage.total_tokens === 'number' ? usage.total_tokens : null

  return (
    <section className="space-y-2 rounded-md border border-sky-500/30 bg-sky-500/[0.03] p-2.5 sm:p-3">
      <div className="flex flex-wrap items-center gap-2">
        <Code2 className="h-4 w-4 text-sky-600 dark:text-sky-400" />
        <span className="text-sm font-semibold">Responses 原生输出</span>
        <Badge variant="secondary">{outputItems.length} Items</Badge>
        {response.status !== undefined && (
          <Badge variant="outline">{String(response.status)}</Badge>
        )}
        {response.model !== undefined && (
          <span className="text-muted-foreground text-xs">{String(response.model)}</span>
        )}
        {response.id !== undefined && (
          <span className="text-muted-foreground ml-auto font-mono text-[11px]">
            {String(response.id)}
          </span>
        )}
      </div>

      {(inputTokens !== null || outputTokens !== null || totalTokens !== null) && (
        <div className="text-muted-foreground flex flex-wrap gap-x-3 gap-y-1 text-xs">
          {inputTokens !== null && <span>输入 {inputTokens}</span>}
          {outputTokens !== null && <span>输出 {outputTokens}</span>}
          {totalTokens !== null && <span>总计 {totalTokens}</span>}
        </div>
      )}

      {outputItems.length > 0 ? (
        <div className="space-y-2">
          {outputItems.map((rawItem, index) => {
            const item = isRecord(rawItem) ? rawItem : { value: rawItem }
            const itemType = String(item.type || '').trim()
            const readableText = getProviderItemReadableText(item, itemType)
            const itemPayload = getProviderItemPayload(item, itemType)
            const itemId = String(item.id || item.call_id || '').trim()
            const itemStatus = String(item.status || '').trim()

            return (
              <article
                key={`${itemId || itemType || 'item'}-${index}`}
                className="bg-background/75 space-y-2 rounded-md border p-2.5 sm:p-3"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <Badge variant="outline">#{index + 1}</Badge>
                  <Badge variant="secondary" className="font-mono">
                    {getProviderItemTypeLabel(itemType)}
                  </Badge>
                  {itemType && (
                    <span className="text-muted-foreground font-mono text-[11px]">{itemType}</span>
                  )}
                  {itemStatus && <Badge variant="outline">{itemStatus}</Badge>}
                  {itemId && (
                    <span className="text-muted-foreground ml-auto font-mono text-[11px]">
                      {itemId}
                    </span>
                  )}
                </div>

                {readableText && (
                  <pre className="bg-muted/20 max-h-96 overflow-auto rounded-md border p-2.5 text-sm leading-6 whitespace-pre-wrap">
                    {readableText}
                  </pre>
                )}

                {itemPayload !== null && itemPayload !== undefined && (
                  <pre className="bg-muted/20 max-h-80 overflow-auto rounded-md border p-2.5 font-mono text-xs leading-5 whitespace-pre-wrap">
                    {stringifyStructuredValue(itemPayload)}
                  </pre>
                )}

                <Collapsible className="rounded-md border">
                  <CollapsibleTrigger asChild>
                    <button
                      type="button"
                      className="hover:bg-muted/50 flex w-full items-center justify-between gap-2 px-2.5 py-1.5 text-left text-xs transition-colors [&[data-state=open]>svg]:rotate-180"
                    >
                      <span>完整 Item JSON</span>
                      <ChevronDown className="h-3.5 w-3.5 shrink-0 transition-transform" />
                    </button>
                  </CollapsibleTrigger>
                  <CollapsibleContent className="border-t">
                    <pre className="max-h-96 overflow-auto p-2.5 font-mono text-xs leading-5 whitespace-pre-wrap">
                      {JSON.stringify(rawItem, null, 2)}
                    </pre>
                  </CollapsibleContent>
                </Collapsible>
              </article>
            )
          })}
        </div>
      ) : (
        <p className="text-muted-foreground text-xs">Provider 响应未包含 output Items。</p>
      )}

      <Collapsible className="rounded-md border">
        <CollapsibleTrigger asChild>
          <button
            type="button"
            className="hover:bg-muted/50 flex w-full items-center justify-between gap-2 px-2.5 py-2 text-left text-sm transition-colors [&[data-state=open]>svg]:rotate-180"
          >
            <span className="font-medium">完整 Responses JSON</span>
            <ChevronDown className="h-4 w-4 shrink-0 transition-transform" />
          </button>
        </CollapsibleTrigger>
        <CollapsibleContent className="border-t">
          <pre className="max-h-[36rem] overflow-auto p-2.5 font-mono text-xs leading-5 whitespace-pre-wrap">
            {JSON.stringify(response, null, 2)}
          </pre>
        </CollapsibleContent>
      </Collapsible>
    </section>
  )
}

export function GenerationAttemptTimeline({ attempts }: { attempts: GenerationAttemptSnapshot[] }) {
  if (attempts.length === 0) {
    return (
      <div className="text-muted-foreground rounded-md border border-dashed px-3 py-4 text-sm">
        这条记录没有 Provider 调用诊断。
      </div>
    )
  }

  let finalSucceededIndex = -1
  attempts.forEach((attempt, index) => {
    if (['succeeded', 'completed'].includes(attempt.status)) finalSucceededIndex = index
  })

  return (
    <section className="space-y-2" aria-label="Generation Attempt 时间线">
      {attempts.map((attempt, index) => {
        const succeeded = ['succeeded', 'completed'].includes(attempt.status)
        const defaultOpen =
          index === (finalSucceededIndex >= 0 ? finalSucceededIndex : attempts.length - 1)
        return (
          <Collapsible
            key={attempt.attempt_id || `${attempt.provider_attempt}-${index}`}
            defaultOpen={defaultOpen}
          >
            <article className="bg-muted/10 overflow-hidden rounded-md border">
              <CollapsibleTrigger asChild>
                <button
                  type="button"
                  className="flex w-full flex-wrap items-center gap-2 px-3 py-2.5 text-left"
                >
                  {succeeded ? (
                    <CheckCircle2 className="h-4 w-4 text-emerald-600 dark:text-emerald-400" />
                  ) : (
                    <AlertTriangle className="text-destructive h-4 w-4" />
                  )}
                  <span className="text-sm font-semibold">调用 #{index + 1}</span>
                  <Badge variant="outline" className={getLlmRequestStatusStyle(attempt.status)}>
                    {formatLlmRequestStatus(attempt.status)}
                  </Badge>
                  <Badge variant="secondary">工作流 {attempt.workflow_attempt}</Badge>
                  <span className="text-muted-foreground text-xs">
                    Provider {attempt.provider_attempt} / 模型内 {attempt.model_attempt}
                  </span>
                  <span className="text-muted-foreground text-xs">
                    {attempt.model || '未知模型'}
                  </span>
                  <span className="text-muted-foreground ml-auto text-xs">
                    {attempt.duration_ms > 0
                      ? formatDurationMs(attempt.duration_ms)
                      : formatSnapshotTime(attempt.started_at)}
                  </span>
                  <ChevronDown className="text-muted-foreground h-4 w-4 transition-transform [[data-state=open]>&]:rotate-180" />
                </button>
              </CollapsibleTrigger>
              <CollapsibleContent>
                <div className="space-y-3 border-t p-3">
                  <div className="text-muted-foreground grid gap-1 text-xs sm:grid-cols-2">
                    <span>用途：{attempt.workflow_purpose || '未知'}</span>
                    <span>
                      Provider：{attempt.provider || '未知'} / {attempt.client_type || '未知客户端'}
                    </span>
                    <span>
                      操作：{attempt.operation || '未知'} / {attempt.wire_protocol || '未知协议'}
                    </span>
                    <span className="break-all">Endpoint：{attempt.endpoint || '未知'}</span>
                  </div>

                  {attempt.error && (
                    <div className="border-destructive/30 bg-destructive/10 space-y-1 rounded-md border px-3 py-2 text-sm">
                      <div className="flex flex-wrap items-center gap-2">
                        {attempt.error.type && (
                          <code className="text-xs">{attempt.error.type}</code>
                        )}
                        {attempt.error.status_code !== null &&
                          attempt.error.status_code !== undefined && (
                            <Badge variant="outline">HTTP {attempt.error.status_code}</Badge>
                          )}
                      </div>
                      {attempt.error.message && (
                        <p className="text-destructive break-words">{attempt.error.message}</p>
                      )}
                    </div>
                  )}

                  <ContextItemTimeline
                    title="实际请求 Items"
                    items={attempt.request_items}
                    avatarMap={{}}
                    botSelfNames={new Set<string>()}
                  />
                  {attempt.tool_definitions.length > 0 && (
                    <ToolDefinitionsCollapsible toolDefinitions={attempt.tool_definitions} />
                  )}
                  <details className="rounded-md border">
                    <summary className="cursor-pointer px-3 py-2 text-xs font-medium">
                      请求参数与 Wire Request
                    </summary>
                    <pre className="max-h-96 overflow-auto border-t p-3 font-mono text-xs whitespace-pre-wrap">
                      {stringifyStructuredValue({
                        request_parameters: attempt.request_parameters,
                        wire_request: attempt.wire_request,
                      })}
                    </pre>
                  </details>
                  {attempt.wire_response !== null && attempt.wire_response !== undefined && (
                    <details className="rounded-md border">
                      <summary className="cursor-pointer px-3 py-2 text-xs font-medium">
                        Wire Response / 可用失败响应
                      </summary>
                      <pre className="max-h-96 overflow-auto border-t p-3 font-mono text-xs whitespace-pre-wrap">
                        {stringifyStructuredValue(attempt.wire_response)}
                      </pre>
                    </details>
                  )}

                  <ContextItemTimeline
                    title="Provider 原始输出 Items"
                    items={attempt.output_items}
                    avatarMap={{}}
                    botSelfNames={new Set<string>()}
                  />
                  {attempt.trace && <GenerationTraceCard trace={attempt.trace} />}
                </div>
              </CollapsibleContent>
            </article>
          </Collapsible>
        )
      })}
    </section>
  )
}
