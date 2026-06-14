import { useMemo, useState } from 'react'

import { Check, ChevronLeft, ChevronRight, Loader2, RefreshCw, Search, SlidersHorizontal, Upload } from 'lucide-react'

import { MemoryMiniTabs } from '@/components/memory/MemoryMiniTabs'
import { MemoryProgressIndicator } from '@/components/memory/MemoryProgressIndicator'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Progress } from '@/components/ui/progress'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Tabs, TabsContent } from '@/components/ui/tabs'
import { Textarea } from '@/components/ui/textarea'
import { ThinkingIllustration } from '@/components/ui/thinking-illustration'
import { cn } from '@/lib/utils'
import type {
  MemoryImportChatTargetPayload,
  MemoryImportTaskKind,
} from '@/lib/memory-api'

import { IMPORT_CHUNK_PAGE_SIZE, IMPORT_KIND_OPTIONS, RUNNING_IMPORT_STATUS } from '../constants'
import type { UseImportFormResult } from '../hooks/useImportForm'
import type { UseImportQueueResult } from '../hooks/useImportQueue'
import {
  formatImportTime,
  formatProgressPercent,
  getImportStatusLabel,
  getImportStatusVariant,
  getImportStepLabel,
  normalizeImportInputMode,
  normalizeProgress,
} from '../utils'

function formatChunkSummary(done: unknown, total: unknown, failed: unknown, cancelled: unknown = 0): string {
  const doneCount = Number(done ?? 0)
  const totalCount = Number(total ?? 0)
  const failedCount = Number(failed ?? 0)
  const cancelledCount = Number(cancelled ?? 0)
  const parts = [`成功 ${doneCount} / ${totalCount} 分块`]
  if (failedCount > 0) {
    parts.push(`失败 ${failedCount}`)
  }
  if (cancelledCount > 0) {
    parts.push(`取消 ${cancelledCount}`)
  }
  return parts.join(' · ')
}

function compactTextParts(parts: Array<string | null | undefined>): string[] {
  return parts.map((part) => String(part ?? '').trim()).filter(Boolean)
}

function getUserIdLabel(chat: MemoryImportChatTargetPayload): string {
  const userId = String(chat.user_id ?? '').trim()
  if (!userId) {
    return ''
  }

  const platform = String(chat.platform ?? '').trim().toLowerCase()
  if (platform === 'qq') {
    return `QQ ${userId}`
  }
  if (platform === 'wechat' || platform === 'wx') {
    return `微信 ${userId}`
  }
  return `用户 ID ${userId}`
}

function getChatTargetMetaParts(chat: MemoryImportChatTargetPayload): string[] {
  return compactTextParts([
    chat.platform || '未知平台',
    chat.is_group ? '群聊' : '私聊',
    chat.group_id ? `群号 ${chat.group_id}` : '',
    getUserIdLabel(chat),
  ])
}

function getChatTargetSearchText(chat: MemoryImportChatTargetPayload): string {
  return compactTextParts([
    chat.chat_name,
    chat.platform,
    chat.group_id,
    chat.user_id,
    chat.account_id,
    chat.scope,
    chat.chat_id,
  ])
    .join(' ')
    .toLowerCase()
}

function getChatTargetValueLabel(chat: MemoryImportChatTargetPayload | undefined): string {
  if (!chat) {
    return '不绑定聊天流'
  }
  const idLabel = chat.group_id || chat.user_id
  return idLabel ? `${chat.chat_name} · ${idLabel}` : chat.chat_name
}

function filterChatTargets(
  targets: MemoryImportChatTargetPayload[],
  query: string,
): MemoryImportChatTargetPayload[] {
  const normalizedQuery = query.trim().toLowerCase()
  if (!normalizedQuery) {
    return targets.slice(0, 8)
  }
  return targets.filter((chat) => getChatTargetSearchText(chat).includes(normalizedQuery)).slice(0, 12)
}

export interface ImportTabProps {
  queue: UseImportQueueResult
  form: UseImportFormResult
}

export function ImportTab({ queue, form }: ImportTabProps) {
  const {
    refreshImportQueue,
    runningImportTasks,
    queuedImportTasks,
    recentImportTasks,
    selectedImportTaskId,
    selectImportTask,
    importAutoPolling,
    setImportAutoPolling,
    importPollInterval,
    importErrorText,
    cancelSelectedImportTask,
    retrySelectedImportTask,
    selectedImportTaskLoading,
    selectedImportTaskResolved,
    selectedImportRetrySummary,
    selectedImportTaskErrorText,
    selectedImportFiles,
    selectedImportFileId,
    selectImportFile,
    importChunkTotal,
    importChunkOffset,
    moveImportChunkPage,
    canImportChunkPrev,
    canImportChunkNext,
    importChunksLoading,
    selectedImportChunks,
  } = queue
  const {
    importCreateMode,
    setImportCreateMode,
    importSettings,
    importChatTargets,
    importCommonFileConcurrency,
    setImportCommonFileConcurrency,
    importCommonChunkConcurrency,
    setImportCommonChunkConcurrency,
    importCommonNarrativeWindowSize,
    setImportCommonNarrativeWindowSize,
    importCommonNarrativeOverlap,
    setImportCommonNarrativeOverlap,
    importCommonFactualTargetSize,
    setImportCommonFactualTargetSize,
    importCommonLlmEnabled,
    setImportCommonLlmEnabled,
    importCommonStrategyOverride,
    setImportCommonStrategyOverride,
    importCommonDedupePolicy,
    setImportCommonDedupePolicy,
    importCommonChatLog,
    setImportCommonChatLog,
    importCommonChatId,
    setImportCommonChatId,
    importCommonChatReferenceTime,
    setImportCommonChatReferenceTime,
    importCommonForce,
    setImportCommonForce,
    importCommonClearManifest,
    setImportCommonClearManifest,
    uploadInputMode,
    setUploadInputMode,
    uploadFiles,
    setUploadFiles,
    pasteName,
    setPasteName,
    pasteMode,
    setPasteMode,
    pasteContent,
    setPasteContent,
    rawAlias,
    setRawAlias,
    rawInputMode,
    setRawInputMode,
    rawRelativePath,
    setRawRelativePath,
    rawGlob,
    setRawGlob,
    rawRecursive,
    setRawRecursive,
    openieAlias,
    setOpenieAlias,
    openieRelativePath,
    setOpenieRelativePath,
    openieIncludeAllJson,
    setOpenieIncludeAllJson,
    convertAlias,
    setConvertAlias,
    convertTargetAlias,
    setConvertTargetAlias,
    convertRelativePath,
    setConvertRelativePath,
    convertTargetRelativePath,
    setConvertTargetRelativePath,
    convertDimension,
    setConvertDimension,
    convertBatchSize,
    setConvertBatchSize,
    backfillAlias,
    setBackfillAlias,
    backfillLimit,
    setBackfillLimit,
    backfillRelativePath,
    setBackfillRelativePath,
    backfillDryRun,
    setBackfillDryRun,
    backfillNoCreatedFallback,
    setBackfillNoCreatedFallback,
    maibotSourceDb,
    setMaibotSourceDb,
    maibotTimeFrom,
    setMaibotTimeFrom,
    maibotTimeTo,
    setMaibotTimeTo,
    maibotStartId,
    setMaibotStartId,
    maibotEndId,
    setMaibotEndId,
    maibotStreamIds,
    setMaibotStreamIds,
    maibotGroupIds,
    setMaibotGroupIds,
    maibotUserIds,
    setMaibotUserIds,
    maibotReadBatchSize,
    setMaibotReadBatchSize,
    maibotCommitWindowRows,
    setMaibotCommitWindowRows,
    maibotEmbedWorkers,
    setMaibotEmbedWorkers,
    maibotNoResume,
    setMaibotNoResume,
    maibotResetState,
    setMaibotResetState,
    maibotDryRun,
    setMaibotDryRun,
    maibotVerifyOnly,
    setMaibotVerifyOnly,
    submitImportByMode,
    creatingImport,
    pathResolveAlias,
    setPathResolveAlias,
    importAliasKeys,
    pathResolveRelativePath,
    setPathResolveRelativePath,
    pathResolveMustExist,
    setPathResolveMustExist,
    resolveImportPath,
    resolvingPath,
    pathResolveOutput,
  } = form
  const [chatTargetQuery, setChatTargetQuery] = useState('')
  const selectedImportChatTarget = useMemo(
    () => importChatTargets.find((chat) => chat.chat_id === importCommonChatId.trim()),
    [importChatTargets, importCommonChatId],
  )
  const visibleImportChatTargets = useMemo(
    () => filterChatTargets(importChatTargets, chatTargetQuery),
    [chatTargetQuery, importChatTargets],
  )
  const importMaxChunkChars = Number.isFinite(Number(importSettings.max_chunk_chars))
    ? Number(importSettings.max_chunk_chars)
    : 3200
  const narrativeWindowForOverlap = Number.isFinite(
    Number(importCommonNarrativeWindowSize || importSettings.default_narrative_window_size),
  )
    ? Number(importCommonNarrativeWindowSize || importSettings.default_narrative_window_size)
    : 1600

  return (
    <TabsContent
      value="import"
      className="space-y-6 [&_input]:h-10 [&_[role=combobox]]:h-10 [&_textarea]:min-h-[96px]"
    >
      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <div className="order-2 space-y-6 lg:order-1">
          <Card className="rounded-2xl border-border/70 shadow-sm">
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Upload className="h-4 w-4" />
                创建导入任务
              </CardTitle>
              <CardDescription>按“选择导入方式 → 检查公共参数 → 创建任务”的顺序完成导入。</CardDescription>
            </CardHeader>
            <CardContent className="space-y-6">
              <Tabs
                value={importCreateMode}
                onValueChange={(value) => setImportCreateMode(value as MemoryImportTaskKind)}
                className="space-y-4"
              >
                <div className="space-y-2">
                  <Label>选择导入方式</Label>
                  <MemoryMiniTabs items={IMPORT_KIND_OPTIONS} />
                </div>

                <div className="space-y-4 rounded-lg border bg-muted/20 p-4">
                <div className="rounded-md border border-border/60 bg-background/80 px-3 py-2">
                  <div className="text-sm font-medium text-foreground">公共参数</div>
                  <div className="mt-0.5 text-xs leading-relaxed text-foreground/75">这些设置会应用到当前导入任务。一般保持默认即可，只在批量导入或排查问题时调整。</div>
                </div>
                <div className="grid gap-3 md:grid-cols-2">
                  <div className="grid gap-2 rounded-md border bg-background/70 p-3 sm:grid-cols-[minmax(0,1fr)_8rem] sm:items-center">
                    <div className="min-w-0">
                      <Label>文件并发数</Label>
                      <div className="mt-0.5 text-xs text-muted-foreground">同时处理多少个文件；文件很多时再适当调高。</div>
                    </div>
                    <Input
                      type="number"
                      min={1}
                      max={Number(importSettings.max_file_concurrency ?? 128)}
                      value={importCommonFileConcurrency}
                      onChange={(event) => setImportCommonFileConcurrency(event.target.value)}
                    />
                  </div>
                  <div className="grid gap-2 rounded-md border bg-background/70 p-3 sm:grid-cols-[minmax(0,1fr)_8rem] sm:items-center">
                    <div className="min-w-0">
                      <Label>分块并发数</Label>
                      <div className="mt-0.5 text-xs text-muted-foreground">单个文件内并行处理多少个分块；过高会增加资源占用。</div>
                    </div>
                    <Input
                      type="number"
                      min={1}
                      max={Number(importSettings.max_chunk_concurrency ?? 256)}
                      value={importCommonChunkConcurrency}
                      onChange={(event) => setImportCommonChunkConcurrency(event.target.value)}
                    />
                  </div>
                  <div className="rounded-md border bg-background/70 px-2.5 py-2">
                    <div className="flex items-center gap-2 text-sm font-medium leading-tight">
                      <Checkbox
                        checked={importCommonLlmEnabled}
                        onCheckedChange={(value) => setImportCommonLlmEnabled(Boolean(value))}
                      />
                      启用 LLM 抽取
                    </div>
                    <div className="mt-0.5 pl-6 text-[11px] leading-snug text-muted-foreground">需要模型参与抽取，质量更高但耗时更长。</div>
                  </div>
                  <div className="rounded-md border bg-background/70 px-2.5 py-2">
                    <div className="flex items-center gap-2 text-sm font-medium leading-tight">
                      <Checkbox
                        checked={importCommonChatLog}
                        onCheckedChange={(value) => setImportCommonChatLog(Boolean(value))}
                      />
                      按聊天日志解析
                    </div>
                    <div className="mt-0.5 pl-6 text-[11px] leading-snug text-muted-foreground">适合导入聊天记录，会尽量保留时间和对话上下文。</div>
                  </div>
                  <div className="grid gap-3 rounded-md border bg-background/70 p-3 md:col-span-2 md:grid-cols-[minmax(0,1fr)_minmax(18rem,28rem)]">
                    <div className="min-w-0">
                      <Label>归属聊天流</Label>
                      <div className="mt-0.5 text-xs text-muted-foreground">可输入群号、QQ 号或聊天名检索；选择后，这批记忆只会在对应聊天流的检索中默认出现。</div>
                    </div>
                    <div className="space-y-2">
                      <div className="relative">
                        <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                        <Input
                          aria-label="搜索归属聊天流"
                          value={chatTargetQuery}
                          onChange={(event) => setChatTargetQuery(event.target.value)}
                          placeholder="输入群号、QQ 号或聊天名"
                          className="pl-9"
                        />
                      </div>
                      <div className="rounded-md border bg-background">
                        <button
                          type="button"
                          className={cn(
                            'flex w-full items-center gap-2 px-3 py-2 text-left text-sm hover:bg-accent',
                            !importCommonChatId && 'bg-accent/70',
                          )}
                          onClick={() => setImportCommonChatId('')}
                        >
                          <Check className={cn('h-4 w-4 shrink-0', !importCommonChatId ? 'opacity-100' : 'opacity-0')} />
                          <span className="truncate">不绑定聊天流</span>
                        </button>
                        {visibleImportChatTargets.length > 0 ? (
                          <div className="max-h-44 overflow-y-auto border-t">
                            {visibleImportChatTargets.map((chat) => (
                              <button
                                key={chat.chat_id}
                                type="button"
                                className={cn(
                                  'flex w-full items-start gap-2 px-3 py-2 text-left text-sm hover:bg-accent',
                                  importCommonChatId.trim() === chat.chat_id && 'bg-accent/70',
                                )}
                                onClick={() => setImportCommonChatId(chat.chat_id)}
                              >
                                <Check
                                  className={cn(
                                    'mt-0.5 h-4 w-4 shrink-0',
                                    importCommonChatId.trim() === chat.chat_id ? 'opacity-100' : 'opacity-0',
                                  )}
                                />
                                <span className="min-w-0 flex-1">
                                  <span className="block truncate font-medium">{chat.chat_name}</span>
                                  <span className="block truncate text-[11px] text-muted-foreground">
                                    {getChatTargetMetaParts(chat).join(' · ')}
                                  </span>
                                </span>
                              </button>
                            ))}
                          </div>
                        ) : (
                          <div className="border-t px-3 py-3 text-sm text-muted-foreground">没有找到匹配的聊天流</div>
                        )}
                      </div>
                      <div className="truncate text-[11px] leading-snug text-muted-foreground">
                        当前选择：{getChatTargetValueLabel(selectedImportChatTarget)}
                      </div>
                    </div>
                  </div>
                </div>

                <details className="rounded-md border bg-background/70 p-3 text-sm">
                  <summary className="cursor-pointer text-xs font-medium text-muted-foreground">
                    高级参数（通常不用修改）
                  </summary>
                  <div className="mt-3 grid gap-3">
                    <div className="grid gap-3 md:grid-cols-3">
                      <div className="space-y-1">
                        <Label>叙事抽取窗口</Label>
                        <Input
                          type="number"
                          min={200}
                          max={importMaxChunkChars}
                          value={importCommonNarrativeWindowSize}
                          onChange={(event) => setImportCommonNarrativeWindowSize(event.target.value)}
                        />
                        <div className="text-[11px] leading-snug text-muted-foreground">
                          默认 {Number(importSettings.default_narrative_window_size ?? 1600)}，用于 narrative/聊天日志。
                        </div>
                      </div>
                      <div className="space-y-1">
                        <Label>叙事重叠字符</Label>
                        <Input
                          type="number"
                          min={0}
                          max={Math.max(0, narrativeWindowForOverlap - 1)}
                          value={importCommonNarrativeOverlap}
                          onChange={(event) => setImportCommonNarrativeOverlap(event.target.value)}
                        />
                        <div className="text-[11px] leading-snug text-muted-foreground">
                          默认 {Number(importSettings.default_narrative_overlap ?? 400)}，保留跨块上下文。
                        </div>
                      </div>
                      <div className="space-y-1">
                        <Label>事实分块目标</Label>
                        <Input
                          type="number"
                          min={200}
                          max={importMaxChunkChars}
                          value={importCommonFactualTargetSize}
                          onChange={(event) => setImportCommonFactualTargetSize(event.target.value)}
                        />
                        <div className="text-[11px] leading-snug text-muted-foreground">
                          默认 {Number(importSettings.default_factual_target_size ?? 1200)}，用于 factual 结构感知切分。
                        </div>
                      </div>
                    </div>
                    <div className="space-y-1">
                      <Label>指定抽取策略</Label>
                      <Input
                        value={importCommonStrategyOverride}
                        onChange={(event) => setImportCommonStrategyOverride(event.target.value)}
                      />
                    </div>
                    <div className="space-y-1">
                      <Label>去重策略</Label>
                      <Input
                        value={importCommonDedupePolicy}
                        onChange={(event) => setImportCommonDedupePolicy(event.target.value)}
                      />
                    </div>
                    <div className="space-y-1">
                      <Label>聊天参考时间</Label>
                      <Input
                        value={importCommonChatReferenceTime}
                        onChange={(event) => setImportCommonChatReferenceTime(event.target.value)}
                      />
                    </div>
                    <div className="space-y-1">
                      <Label>聊天流 ID</Label>
                      <Input
                        value={importCommonChatId}
                        onChange={(event) => setImportCommonChatId(event.target.value)}
                        placeholder="留空表示不绑定"
                      />
                      <div className="text-[11px] leading-snug text-muted-foreground">仅填写已存在的真实聊天流 ID；上方下拉无法覆盖时再手动填写。</div>
                    </div>
                    <div className="flex items-center gap-2 text-sm">
                      <Checkbox
                        checked={importCommonForce}
                        onCheckedChange={(value) => setImportCommonForce(Boolean(value))}
                      />
                      强制导入
                    </div>
                    <div className="flex items-center gap-2 text-sm">
                      <Checkbox
                        checked={importCommonClearManifest}
                        onCheckedChange={(value) => setImportCommonClearManifest(Boolean(value))}
                      />
                      清空导入清单
                    </div>
                  </div>
                </details>
              </div>

              <TabsContent value="upload" className="mt-0">
                <div className="space-y-3 rounded-xl border bg-background/70 p-4">
                  <div className="text-xs text-muted-foreground">选择一个或多个本地文件创建导入任务，适合批量导入资料或聊天记录。</div>
                  <div className="grid gap-3">
                    <div className="space-y-1">
                      <Label>输入模式</Label>
                      <Select
                        value={uploadInputMode}
                        onValueChange={(value) => setUploadInputMode(normalizeImportInputMode(value))}
                      >
                        <SelectTrigger aria-label="upload-input-mode">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="text">文本</SelectItem>
                          <SelectItem value="json">结构化 JSON</SelectItem>
                        </SelectContent>
                      </Select>
                    </div>
                    <div className="space-y-1">
                      <Label>文件选择</Label>
                      <Input
                        type="file"
                        multiple
                        accept=".txt,.md,.json,.jsonl,.csv,.log,.html,.htm,.xml"
                        onChange={(event) => setUploadFiles(Array.from(event.target.files ?? []))}
                      />
                    </div>
                  </div>
                  <div className="text-xs text-muted-foreground">已选择 {uploadFiles.length} 个文件</div>
                </div>
              </TabsContent>

              <TabsContent value="paste" className="mt-0">
                <div className="space-y-3 rounded-xl border bg-background/70 p-4">
                  <div className="text-xs text-muted-foreground">直接粘贴少量文本或 JSON，适合临时补充一段资料。</div>
                  <div className="grid gap-3">
                    <div className="space-y-1">
                      <Label>内容名称</Label>
                      <Input value={pasteName} onChange={(event) => setPasteName(event.target.value)} />
                    </div>
                    <div className="space-y-1">
                      <Label>输入模式</Label>
                      <Select
                        value={pasteMode}
                        onValueChange={(value) => setPasteMode(normalizeImportInputMode(value))}
                      >
                        <SelectTrigger aria-label="paste-input-mode">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="text">文本</SelectItem>
                          <SelectItem value="json">结构化 JSON</SelectItem>
                        </SelectContent>
                      </Select>
                    </div>
                    <div className="space-y-1">
                      <Label>粘贴内容</Label>
                      <Textarea
                        value={pasteContent}
                        onChange={(event) => setPasteContent(event.target.value)}
                        rows={8}
                      />
                    </div>
                  </div>
                </div>
              </TabsContent>

              <TabsContent value="raw_scan" className="mt-0">
                <div className="space-y-3 rounded-xl border bg-background/70 p-4">
                  <div className="text-xs text-muted-foreground">扫描目录文件，适合本地批处理</div>
                  <div className="grid gap-3">
                    <div className="space-y-1">
                      <Label>路径别名</Label>
                      <Input value={rawAlias} onChange={(event) => setRawAlias(event.target.value)} />
                    </div>
                    <div className="space-y-1">
                      <Label>输入模式</Label>
                      <Select
                        value={rawInputMode}
                        onValueChange={(value) => setRawInputMode(normalizeImportInputMode(value))}
                      >
                        <SelectTrigger aria-label="raw-input-mode">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="text">文本</SelectItem>
                          <SelectItem value="json">结构化 JSON</SelectItem>
                        </SelectContent>
                      </Select>
                    </div>
                    <div className="space-y-1">
                      <Label>相对路径</Label>
                      <Input value={rawRelativePath} onChange={(event) => setRawRelativePath(event.target.value)} />
                    </div>
                    <div className="space-y-1">
                      <Label>匹配规则（Glob）</Label>
                      <Input value={rawGlob} onChange={(event) => setRawGlob(event.target.value)} />
                    </div>
                  </div>
                  <div className="flex items-center gap-2 text-sm">
                    <Checkbox checked={rawRecursive} onCheckedChange={(value) => setRawRecursive(Boolean(value))} />
                    递归扫描
                  </div>
                </div>
              </TabsContent>

              <TabsContent value="lpmm_openie" className="mt-0">
                <div className="space-y-3 rounded-xl border bg-background/70 p-4">
                  <div className="text-xs text-muted-foreground">读取 LPMM 内容并抽取关系</div>
                  <div className="grid gap-3">
                    <div className="space-y-1">
                      <Label>路径别名</Label>
                      <Input value={openieAlias} onChange={(event) => setOpenieAlias(event.target.value)} />
                    </div>
                    <div className="space-y-1">
                      <Label>相对路径</Label>
                      <Input value={openieRelativePath} onChange={(event) => setOpenieRelativePath(event.target.value)} />
                    </div>
                  </div>
                  <div className="flex items-center gap-2 text-sm">
                    <Checkbox
                      checked={openieIncludeAllJson}
                      onCheckedChange={(value) => setOpenieIncludeAllJson(Boolean(value))}
                    />
                    包含全部 JSON 文件
                  </div>
                </div>
              </TabsContent>

              <TabsContent value="lpmm_convert" className="mt-0">
                <div className="space-y-3 rounded-xl border bg-background/70 p-4">
                  <div className="text-xs text-muted-foreground">将 LPMM 数据转换到目标目录</div>
                  <div className="grid gap-3">
                    <div className="space-y-1">
                      <Label>源路径别名</Label>
                      <Input value={convertAlias} onChange={(event) => setConvertAlias(event.target.value)} />
                    </div>
                    <div className="space-y-1">
                      <Label>目标路径别名</Label>
                      <Input value={convertTargetAlias} onChange={(event) => setConvertTargetAlias(event.target.value)} />
                    </div>
                    <div className="space-y-1">
                      <Label>源相对路径</Label>
                      <Input value={convertRelativePath} onChange={(event) => setConvertRelativePath(event.target.value)} />
                    </div>
                    <div className="space-y-1">
                      <Label>目标相对路径</Label>
                      <Input
                        value={convertTargetRelativePath}
                        onChange={(event) => setConvertTargetRelativePath(event.target.value)}
                      />
                    </div>
                    <div className="space-y-1">
                      <Label>向量维度</Label>
                      <Input
                        type="number"
                        min={1}
                        value={convertDimension}
                        onChange={(event) => setConvertDimension(event.target.value)}
                      />
                    </div>
                    <div className="space-y-1">
                      <Label>批处理大小</Label>
                      <Input
                        type="number"
                        min={1}
                        value={convertBatchSize}
                        onChange={(event) => setConvertBatchSize(event.target.value)}
                      />
                    </div>
                  </div>
                </div>
              </TabsContent>

              <TabsContent value="temporal_backfill" className="mt-0">
                <div className="space-y-3 rounded-xl border bg-background/70 p-4">
                  <div className="text-xs text-muted-foreground">为已有数据补齐时间字段</div>
                  <div className="grid gap-3">
                    <div className="space-y-1">
                      <Label>路径别名</Label>
                      <Input value={backfillAlias} onChange={(event) => setBackfillAlias(event.target.value)} />
                    </div>
                    <div className="space-y-1">
                      <Label>处理上限</Label>
                      <Input type="number" min={1} value={backfillLimit} onChange={(event) => setBackfillLimit(event.target.value)} />
                    </div>
                    <div className="space-y-1">
                      <Label>相对路径</Label>
                      <Input value={backfillRelativePath} onChange={(event) => setBackfillRelativePath(event.target.value)} />
                    </div>
                  </div>
                  <div className="grid gap-2">
                    <div className="flex items-center gap-2 text-sm">
                      <Checkbox checked={backfillDryRun} onCheckedChange={(value) => setBackfillDryRun(Boolean(value))} />
                      只预演，不写入数据
                    </div>
                    <div className="flex items-center gap-2 text-sm">
                      <Checkbox
                        checked={backfillNoCreatedFallback}
                        onCheckedChange={(value) => setBackfillNoCreatedFallback(Boolean(value))}
                      />
                      禁用创建时间回退
                    </div>
                  </div>
                </div>
              </TabsContent>

              <TabsContent value="maibot_migration" className="mt-0">
                <div className="space-y-3 rounded-xl border bg-background/70 p-4">
                  <div className="text-xs text-muted-foreground">迁移 MaiBot 历史长期记忆</div>
                  <div className="grid gap-3">
                    <div className="space-y-1">
                      <Label htmlFor="maibot-source-db">源数据库路径</Label>
                      <Input
                        id="maibot-source-db"
                        required
                        value={maibotSourceDb}
                        onChange={(event) => setMaibotSourceDb(event.target.value)}
                        placeholder="data/MaiBot.db"
                      />
                    </div>
                    <div className="grid gap-3 md:grid-cols-2">
                      <div className="space-y-1">
                        <Label htmlFor="maibot-time-from">起始时间</Label>
                        <Input
                          id="maibot-time-from"
                          type="datetime-local"
                          step={1}
                          max={maibotTimeTo || undefined}
                          value={maibotTimeFrom}
                          onChange={(event) => setMaibotTimeFrom(event.target.value)}
                        />
                      </div>
                      <div className="space-y-1">
                        <Label htmlFor="maibot-time-to">结束时间</Label>
                        <Input
                          id="maibot-time-to"
                          type="datetime-local"
                          step={1}
                          min={maibotTimeFrom || undefined}
                          value={maibotTimeTo}
                          onChange={(event) => setMaibotTimeTo(event.target.value)}
                        />
                      </div>
                    </div>
                    <div className="grid gap-3 md:grid-cols-2">
                      <div className="space-y-1">
                        <Label htmlFor="maibot-start-id">起始 ID</Label>
                        <Input
                          id="maibot-start-id"
                          type="number"
                          min={1}
                          max={maibotEndId || undefined}
                          step={1}
                          value={maibotStartId}
                          onChange={(event) => setMaibotStartId(event.target.value)}
                        />
                      </div>
                      <div className="space-y-1">
                        <Label htmlFor="maibot-end-id">结束 ID</Label>
                        <Input
                          id="maibot-end-id"
                          type="number"
                          min={maibotStartId || 1}
                          step={1}
                          value={maibotEndId}
                          onChange={(event) => setMaibotEndId(event.target.value)}
                        />
                      </div>
                    </div>
                    <div className="space-y-1">
                      <Label htmlFor="maibot-stream-ids">会话 ID 列表</Label>
                      <Input
                        id="maibot-stream-ids"
                        value={maibotStreamIds}
                        onChange={(event) => setMaibotStreamIds(event.target.value)}
                      />
                    </div>
                    <div className="space-y-1">
                      <Label htmlFor="maibot-group-ids">群组 ID 列表</Label>
                      <Input
                        id="maibot-group-ids"
                        value={maibotGroupIds}
                        onChange={(event) => setMaibotGroupIds(event.target.value)}
                      />
                    </div>
                    <div className="space-y-1">
                      <Label htmlFor="maibot-user-ids">用户 ID 列表</Label>
                      <Input
                        id="maibot-user-ids"
                        value={maibotUserIds}
                        onChange={(event) => setMaibotUserIds(event.target.value)}
                      />
                    </div>
                  </div>
                  <details className="rounded-md border bg-background/70 p-3 text-sm">
                    <summary className="flex cursor-pointer items-center gap-2 text-sm font-medium">
                      <SlidersHorizontal className="h-4 w-4" />
                      高级选项
                    </summary>
                    <div className="mt-3 grid gap-3">
                      <div className="space-y-1">
                        <Label htmlFor="maibot-read-batch-size">读取批大小</Label>
                        <Input
                          id="maibot-read-batch-size"
                          type="number"
                          min={1}
                          step={1}
                          value={maibotReadBatchSize}
                          onChange={(event) => setMaibotReadBatchSize(event.target.value)}
                        />
                      </div>
                      <div className="space-y-1">
                        <Label htmlFor="maibot-commit-window-rows">提交窗口行数</Label>
                        <Input
                          id="maibot-commit-window-rows"
                          type="number"
                          min={1}
                          step={1}
                          value={maibotCommitWindowRows}
                          onChange={(event) => setMaibotCommitWindowRows(event.target.value)}
                        />
                      </div>
                      <div className="space-y-1">
                        <Label htmlFor="maibot-embed-workers">向量线程数</Label>
                        <Input
                          id="maibot-embed-workers"
                          type="number"
                          min={1}
                          step={1}
                          value={maibotEmbedWorkers}
                          onChange={(event) => setMaibotEmbedWorkers(event.target.value)}
                        />
                      </div>
                      <div className="grid gap-2">
                        <div className="flex items-center gap-2 text-sm">
                          <Checkbox checked={maibotNoResume} onCheckedChange={(value) => setMaibotNoResume(Boolean(value))} />
                          从头开始，不继续上次进度
                        </div>
                        <div className="flex items-center gap-2 text-sm">
                          <Checkbox checked={maibotResetState} onCheckedChange={(value) => setMaibotResetState(Boolean(value))} />
                          重置迁移状态
                        </div>
                      </div>
                    </div>
                  </details>
                  <div className="grid gap-2">
                    <div className="flex items-center gap-2 text-sm">
                      <Checkbox checked={maibotDryRun} onCheckedChange={(value) => setMaibotDryRun(Boolean(value))} />
                      只预演，不写入数据
                    </div>
                    <div className="flex items-center gap-2 text-sm">
                      <Checkbox checked={maibotVerifyOnly} onCheckedChange={(value) => setMaibotVerifyOnly(Boolean(value))} />
                      仅校验
                    </div>
                  </div>
                </div>
              </TabsContent>

              </Tabs>

              <Button onClick={() => void submitImportByMode()} disabled={creatingImport}>
                {creatingImport ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Upload className="mr-2 h-4 w-4" />}
                创建导入任务
              </Button>
            </CardContent>
          </Card>

          <Card className="rounded-2xl border-border/70 bg-card/85 shadow-sm">
            <CardHeader>
              <CardTitle>路径预检</CardTitle>
              <CardDescription>在创建本地扫描、转换或迁移任务前，先确认路径会被解析到哪里。</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid gap-3">
                <div className="space-y-1">
                  <Label>路径别名</Label>
                  <div className="text-xs text-muted-foreground">选择后端允许访问的数据根目录。</div>
                  <Select value={pathResolveAlias} onValueChange={setPathResolveAlias}>
                    <SelectTrigger aria-label="import-path-alias">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {importAliasKeys.length > 0 ? importAliasKeys.map((alias) => (
                        <SelectItem key={alias} value={alias}>{alias}</SelectItem>
                      )) : (
                        <SelectItem value="raw">raw</SelectItem>
                      )}
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-1">
                  <Label>相对路径</Label>
                  <div className="text-xs text-muted-foreground">填写相对于路径别名的子路径，不需要填写完整磁盘路径。</div>
                  <Input
                    value={pathResolveRelativePath}
                    onChange={(event) => setPathResolveRelativePath(event.target.value)}
                    placeholder="例如 exports/weekly"
                  />
                </div>
              </div>
              <div className="flex items-center gap-2 text-sm">
                <Checkbox checked={pathResolveMustExist} onCheckedChange={(value) => setPathResolveMustExist(Boolean(value))} />
                要求路径已存在
              </div>
              <Button
                variant="outline"
                onClick={() => void resolveImportPath()}
                disabled={resolvingPath || !pathResolveAlias.trim()}
              >
                {resolvingPath ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-2 h-4 w-4" />}
                解析路径
              </Button>
              <Textarea value={pathResolveOutput} readOnly rows={6} placeholder="解析结果会显示在这里" />
            </CardContent>
          </Card>
        </div>

        <div className="order-1 space-y-6 lg:order-2">
          <Card className="rounded-2xl border-border/70 bg-card/90 shadow-sm">
            <CardHeader className="space-y-4 pb-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <CardTitle>导入队列</CardTitle>
                <Button variant="outline" size="sm" onClick={() => void refreshImportQueue()}>
                  <RefreshCw className="mr-2 h-4 w-4" />
                  刷新
                </Button>
              </div>
              <div className="flex flex-wrap items-center justify-between gap-3">
                <CardDescription className="text-sm">
                  查看任务是否正在运行、排队等待或已经结束。点击任务卡片可查看详情。
                </CardDescription>
                <div className="flex flex-wrap gap-2 text-xs text-muted-foreground">
                  <Badge variant="outline" className="bg-background/70">运行中 {runningImportTasks.length}</Badge>
                  <Badge variant="outline" className="bg-background/70">排队中 {queuedImportTasks.length}</Badge>
                  <Badge variant="outline" className="bg-background/70">最近完成 {recentImportTasks.length}</Badge>
                </div>
                <label className="flex items-center gap-2 text-xs text-muted-foreground">
                  <Checkbox checked={importAutoPolling} onCheckedChange={(value) => setImportAutoPolling(Boolean(value))} />
                  自动轮询 {importPollInterval}ms
                </label>
              </div>
            </CardHeader>
            <CardContent className="space-y-6">
              {importErrorText ? (
                <Alert variant="destructive">
                  <AlertDescription>{importErrorText}</AlertDescription>
                </Alert>
              ) : null}

              <div className="space-y-2.5">
                <div className="flex items-center justify-between gap-2">
                  <div className="text-sm font-medium">运行中</div>
                  <Badge variant="outline">{runningImportTasks.length}</Badge>
                </div>
                {runningImportTasks.length > 0 ? (
                  <ScrollArea className="h-[208px] rounded-xl border bg-muted/10">
                    <div className="space-y-2.5 p-2.5">
                      {runningImportTasks.map((task) => {
                        const isSelected = task.task_id === selectedImportTaskId
                        return (
                          <button
                            key={task.task_id}
                            type="button"
                            onClick={() => void selectImportTask(task.task_id)}
                            className={cn(
                              'w-full rounded-xl border p-4 text-left transition-all',
                              isSelected
                                ? 'border-primary/70 bg-primary/5 shadow-sm'
                                : 'bg-background/80 hover:border-muted-foreground/40 hover:bg-muted/20',
                            )}
                          >
                            <div className="flex flex-wrap items-start justify-between gap-2">
                              <div className="min-w-0 space-y-1">
                                <div className="break-all font-mono text-[11px] leading-relaxed text-muted-foreground">
                                  {task.task_id}
                                </div>
                                <div className="text-sm font-medium">{String(task.task_kind ?? task.mode ?? '-')}</div>
                              </div>
                              <Badge variant={getImportStatusVariant(String(task.status ?? ''))}>
                                {getImportStatusLabel(String(task.status ?? ''))}
                              </Badge>
                            </div>
                            <div className="mt-2 flex items-center justify-between gap-2 text-xs text-muted-foreground">
                              <span>{getImportStepLabel(String(task.current_step ?? 'running'))}</span>
                              <span>{formatProgressPercent(task.progress)}</span>
                            </div>
                            <Progress value={normalizeProgress(task.progress)} className="mt-2 h-1.5" />
                          </button>
                        )
                      })}
                    </div>
                  </ScrollArea>
                ) : (
                  <div className="rounded-xl border bg-muted/20 p-4 text-sm text-muted-foreground">当前没有运行中任务</div>
                )}
              </div>

              <div className="space-y-2.5">
                <div className="flex items-center justify-between gap-2">
                  <div className="text-sm font-medium">排队中</div>
                  <Badge variant="outline">{queuedImportTasks.length}</Badge>
                </div>
                {queuedImportTasks.length > 0 ? (
                  <ScrollArea className="h-[188px] rounded-xl border bg-muted/10">
                    <div className="space-y-2.5 p-2.5">
                      {queuedImportTasks.map((task) => {
                        const isSelected = task.task_id === selectedImportTaskId
                        return (
                          <button
                            key={task.task_id}
                            type="button"
                            onClick={() => void selectImportTask(task.task_id)}
                            className={cn(
                              'w-full rounded-xl border p-4 text-left transition-all',
                              isSelected
                                ? 'border-primary/70 bg-primary/5 shadow-sm'
                                : 'bg-background/80 hover:border-muted-foreground/40 hover:bg-muted/20',
                            )}
                          >
                            <div className="flex flex-wrap items-start justify-between gap-2">
                              <div className="min-w-0 space-y-1">
                                <div className="break-all font-mono text-[11px] leading-relaxed text-muted-foreground">
                                  {task.task_id}
                                </div>
                                <div className="text-sm font-medium">{String(task.task_kind ?? task.mode ?? '-')}</div>
                              </div>
                              <Badge variant={getImportStatusVariant(String(task.status ?? ''))}>
                                {getImportStatusLabel(String(task.status ?? ''))}
                              </Badge>
                            </div>
                            <div className="mt-2 flex items-center justify-between gap-2 text-xs text-muted-foreground">
                              <span>创建时间</span>
                              <span>{formatImportTime(task.created_at)}</span>
                            </div>
                          </button>
                        )
                      })}
                    </div>
                  </ScrollArea>
                ) : (
                  <div className="rounded-xl border bg-muted/20 p-4 text-sm text-muted-foreground">当前没有排队任务</div>
                )}
              </div>

              <div className="space-y-2.5">
                <div className="flex items-center justify-between gap-2">
                  <div className="text-sm font-medium">最近完成</div>
                  <Badge variant="secondary">{recentImportTasks.length}</Badge>
                </div>
                {recentImportTasks.length > 0 ? (
                  <ScrollArea className="h-[260px] rounded-xl border bg-muted/10">
                    <div className="space-y-2.5 p-2.5">
                      {recentImportTasks.map((task) => {
                        const isSelected = task.task_id === selectedImportTaskId
                        return (
                          <button
                            key={task.task_id}
                            type="button"
                            onClick={() => void selectImportTask(task.task_id)}
                            className={cn(
                              'w-full rounded-xl border p-4 text-left transition-all',
                              isSelected
                                ? 'border-primary/70 bg-primary/5 shadow-sm'
                                : 'bg-background/80 hover:border-muted-foreground/40 hover:bg-muted/20',
                            )}
                          >
                            <div className="flex flex-wrap items-start justify-between gap-2">
                              <div className="min-w-0 space-y-1">
                                <div className="break-all font-mono text-[11px] leading-relaxed text-muted-foreground">
                                  {task.task_id}
                                </div>
                                <div className="text-sm font-medium">{String(task.task_kind ?? task.mode ?? '-')}</div>
                              </div>
                              <Badge variant={getImportStatusVariant(String(task.status ?? ''))}>
                                {getImportStatusLabel(String(task.status ?? ''))}
                              </Badge>
                            </div>
                            <div className="mt-2 flex items-center justify-between gap-2 text-xs text-muted-foreground">
                              <span>完成进度</span>
                              <span>{formatProgressPercent(task.progress)}</span>
                            </div>
                            <Progress value={normalizeProgress(task.progress)} className="mt-2 h-1.5" />
                          </button>
                        )
                      })}
                    </div>
                  </ScrollArea>
                ) : (
                  <div className="rounded-xl border bg-muted/20 p-4 text-sm text-muted-foreground">暂时没有历史任务</div>
                )}
              </div>
            </CardContent>
          </Card>
        </div>
      </div>

      <Card className="rounded-2xl border-border/70 bg-card/90 shadow-sm">
          <CardHeader className="space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <CardTitle>任务详情</CardTitle>
              <div className="flex flex-wrap gap-2">
                <Button
                  size="sm"
                  variant="outline"
                  aria-label="取消选中导入任务"
                  onClick={() => void cancelSelectedImportTask()}
                  disabled={!selectedImportTaskId}
                >
                  取消任务
                </Button>
                <Button
                  size="sm"
                  aria-label="重试选中导入任务"
                  onClick={() => void retrySelectedImportTask()}
                  disabled={!selectedImportTaskId}
                >
                  重试失败项
                </Button>
              </div>
            </div>
            <CardDescription>支持文件级和分块级状态观察，可直接在当前页面定位失败原因</CardDescription>
          </CardHeader>
          <CardContent className="space-y-6">
            {selectedImportTaskLoading ? (
              <div className="flex items-center gap-2">
                <ThinkingIllustration size="sm" />
              </div>
            ) : null}

            {!selectedImportTaskResolved ? (
              <div className="flex flex-col items-center justify-center gap-3 rounded-xl border border-dashed bg-muted/15 px-6 py-10 text-center">
                <div className="rounded-full bg-muted/40 p-3">
                  <Loader2 className="h-5 w-5 text-muted-foreground" />
                </div>
                <div className="space-y-1">
                  <div className="text-sm font-medium">还没选中任务</div>
                  <div className="text-xs leading-relaxed text-muted-foreground">
                    在左侧/上方的导入队列里点击任意任务卡片<br />
                    即可在这里查看进度、文件状态和分块详情
                  </div>
                </div>
              </div>
            ) : (
              <>
                <div className="space-y-2">
                  <div className="text-sm font-medium">任务摘要</div>
                  <div className="overflow-auto rounded-xl border bg-muted/10">
                    <Table className="min-w-[680px]">
                      <TableBody>
                        <TableRow>
                          <TableCell className="w-[140px] text-muted-foreground">任务 ID</TableCell>
                          <TableCell className="break-all font-mono text-xs leading-relaxed">
                            {selectedImportTaskResolved.task_id}
                          </TableCell>
                        </TableRow>
                        <TableRow>
                          <TableCell className="text-muted-foreground">任务类型</TableCell>
                          <TableCell>{String(selectedImportTaskResolved.task_kind ?? selectedImportTaskResolved.mode ?? '-')}</TableCell>
                        </TableRow>
                        <TableRow>
                          <TableCell className="text-muted-foreground">状态 / 步骤</TableCell>
                          <TableCell>
                            <div className="flex flex-wrap items-center gap-2">
                              <Badge variant={getImportStatusVariant(String(selectedImportTaskResolved.status ?? ''))}>
                                {getImportStatusLabel(String(selectedImportTaskResolved.status ?? ''))}
                              </Badge>
                              <span className="text-xs text-muted-foreground">
                                {getImportStepLabel(String(selectedImportTaskResolved.current_step ?? ''))}
                              </span>
                            </div>
                          </TableCell>
                        </TableRow>
                        <TableRow>
                          <TableCell className="text-muted-foreground">进度</TableCell>
                          <TableCell>
                            <MemoryProgressIndicator
                              value={normalizeProgress(selectedImportTaskResolved.progress)}
                              statusLabel={getImportStatusLabel(String(selectedImportTaskResolved.status ?? ''))}
                              stepLabel={getImportStepLabel(String(selectedImportTaskResolved.current_step ?? ''))}
                              tone={
                                String(selectedImportTaskResolved.status ?? '') === 'completed'
                                  ? 'success'
                                  : String(selectedImportTaskResolved.status ?? '') === 'failed'
                                    ? 'destructive'
                                    : String(selectedImportTaskResolved.status ?? '') === 'completed_with_errors'
                                      ? 'warning'
                                      : String(selectedImportTaskResolved.status ?? '') === 'cancelled'
                                      ? 'muted'
                                      : 'default'
                              }
                              busy={RUNNING_IMPORT_STATUS.has(String(selectedImportTaskResolved.status ?? ''))}
                              detail={formatChunkSummary(
                                selectedImportTaskResolved.done_chunks,
                                selectedImportTaskResolved.total_chunks,
                                selectedImportTaskResolved.failed_chunks,
                                selectedImportTaskResolved.cancelled_chunks,
                              )}
                            />
                          </TableCell>
                        </TableRow>
                        <TableRow>
                          <TableCell className="text-muted-foreground">创建时间</TableCell>
                          <TableCell>{formatImportTime(selectedImportTaskResolved.created_at)}</TableCell>
                        </TableRow>
                        <TableRow>
                          <TableCell className="text-muted-foreground">更新时间</TableCell>
                          <TableCell>{formatImportTime(selectedImportTaskResolved.updated_at)}</TableCell>
                        </TableRow>
                      </TableBody>
                    </Table>
                  </div>
                </div>

                {selectedImportRetrySummary ? (
                  <div className="space-y-2">
                    <div className="text-sm font-medium">重试摘要</div>
                    <div className="overflow-auto rounded-xl border bg-muted/10">
                      <Table>
                        <TableBody>
                          <TableRow>
                            <TableCell className="w-[220px] text-muted-foreground">按分块重试的文件数</TableCell>
                            <TableCell>{Number(selectedImportRetrySummary.chunk_retry_files ?? 0)}</TableCell>
                          </TableRow>
                          <TableRow>
                            <TableCell className="text-muted-foreground">按分块重试的分块数</TableCell>
                            <TableCell>{Number(selectedImportRetrySummary.chunk_retry_chunks ?? 0)}</TableCell>
                          </TableRow>
                          <TableRow>
                            <TableCell className="text-muted-foreground">回退整文件重试数</TableCell>
                            <TableCell>{Number(selectedImportRetrySummary.file_fallback_files ?? 0)}</TableCell>
                          </TableRow>
                          <TableRow>
                            <TableCell className="text-muted-foreground">跳过文件数</TableCell>
                            <TableCell>{Number(selectedImportRetrySummary.skipped_files ?? 0)}</TableCell>
                          </TableRow>
                        </TableBody>
                      </Table>
                    </div>
                  </div>
                ) : null}

                {selectedImportTaskErrorText ? (
                  <Alert variant="destructive">
                    <AlertDescription>{selectedImportTaskErrorText}</AlertDescription>
                  </Alert>
                ) : null}

                <div className="space-y-2.5">
                  <div className="text-sm font-medium">文件状态</div>
                  {selectedImportFiles.length > 0 ? (
                    <ScrollArea className="h-[260px] rounded-xl border bg-muted/10">
                      <div className="space-y-2.5 p-2.5">
                        {selectedImportFiles.map((file) => {
                          const isSelected = file.file_id === selectedImportFileId
                          return (
                            <button
                              key={file.file_id}
                              type="button"
                              onClick={() => void selectImportFile(file.file_id)}
                              className={cn(
                                'w-full rounded-xl border p-4 text-left transition-all',
                                isSelected
                                  ? 'border-primary/70 bg-primary/5 shadow-sm'
                                  : 'bg-background/80 hover:border-muted-foreground/40 hover:bg-muted/20',
                              )}
                            >
                              <div className="flex flex-wrap items-center justify-between gap-2">
                                <span className="truncate text-sm font-medium">{file.name || file.file_id}</span>
                                <Badge variant={getImportStatusVariant(String(file.status ?? ''))}>
                                  {getImportStatusLabel(String(file.status ?? ''))}
                                </Badge>
                              </div>
                              <div className="mt-2 flex items-center justify-between gap-2 text-xs text-muted-foreground">
                                <span>{getImportStepLabel(String(file.current_step ?? ''))}</span>
                                <span>{formatProgressPercent(file.progress)}</span>
                              </div>
                              <Progress value={normalizeProgress(file.progress)} className="mt-2 h-1.5" />
                              <div className="mt-2 text-xs text-muted-foreground">
                                {formatProgressPercent(file.progress)} · {formatChunkSummary(
                                  file.done_chunks,
                                  file.total_chunks,
                                  file.failed_chunks,
                                  file.cancelled_chunks,
                                )}
                              </div>
                              {file.error ? (
                                <div className="mt-2 truncate text-xs text-destructive">{file.error}</div>
                              ) : null}
                            </button>
                          )
                        })}
                      </div>
                    </ScrollArea>
                  ) : (
                    <div className="rounded-xl border bg-muted/20 p-4 text-sm text-muted-foreground">当前任务没有文件明细</div>
                  )}
                </div>

                <div className="space-y-2.5">
                  <div className="flex items-center justify-between gap-2">
                    <div className="text-sm font-medium">分块状态</div>
                    <div className="flex items-center gap-2 text-xs text-muted-foreground">
                      <Button
                        size="icon"
                        variant="outline"
                        aria-label="上一页分块"
                        onClick={() => void moveImportChunkPage(-1)}
                        disabled={!canImportChunkPrev}
                      >
                        <ChevronLeft className="h-4 w-4" />
                      </Button>
                      <span>
                        {importChunkTotal > 0
                          ? `${importChunkOffset + 1}-${Math.min(importChunkOffset + IMPORT_CHUNK_PAGE_SIZE, importChunkTotal)}`
                          : '0-0'}
                        {' / '}
                        {importChunkTotal}
                      </span>
                      <Button
                        size="icon"
                        variant="outline"
                        aria-label="下一页分块"
                        onClick={() => void moveImportChunkPage(1)}
                        disabled={!canImportChunkNext}
                      >
                        <ChevronRight className="h-4 w-4" />
                      </Button>
                    </div>
                  </div>

                  <div className="overflow-auto rounded-xl border bg-background/80">
                    <Table className="min-w-[700px]">
                      <TableHeader>
                        <TableRow>
                          <TableHead className="w-[72px]">序号</TableHead>
                          <TableHead className="w-[108px]">状态</TableHead>
                          <TableHead className="w-[108px]">步骤</TableHead>
                          <TableHead className="w-[84px]">进度</TableHead>
                          <TableHead>错误 / 预览</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {importChunksLoading ? (
                          <TableRow>
                            <TableCell colSpan={5} className="text-center text-muted-foreground">
                              <ThinkingIllustration size="sm" className="mx-auto" />
                            </TableCell>
                          </TableRow>
                        ) : selectedImportChunks.length > 0 ? (
                          selectedImportChunks.map((chunk) => (
                            <TableRow key={chunk.chunk_id}>
                              <TableCell>{chunk.index}</TableCell>
                              <TableCell>{getImportStatusLabel(String(chunk.status ?? ''))}</TableCell>
                              <TableCell>{getImportStepLabel(String(chunk.step ?? ''))}</TableCell>
                              <TableCell>{formatProgressPercent(chunk.progress)}</TableCell>
                              <TableCell className="max-w-[360px]">
                                <div className="space-y-2">
                                  {String(chunk.error ?? '').trim() ? (
                                    <div className="rounded-md border border-destructive/30 bg-destructive/5 px-2.5 py-2 text-sm leading-relaxed text-destructive">
                                      {String(chunk.error)}
                                    </div>
                                  ) : null}
                                  <details className="rounded-md border bg-muted/20 px-2.5 py-2 text-xs text-muted-foreground">
                                    <summary className="cursor-pointer font-medium text-foreground">
                                      {String(chunk.error ?? '').trim() ? '查看分块预览' : '查看内容详情'}
                                    </summary>
                                    <div className="mt-2 whitespace-pre-wrap break-words leading-relaxed">
                                      {String(chunk.content_preview ?? '-') || '-'}
                                    </div>
                                  </details>
                                </div>
                              </TableCell>
                            </TableRow>
                          ))
                        ) : (
                          <TableRow>
                            <TableCell colSpan={5} className="text-center text-muted-foreground">
                              当前页没有分块数据
                            </TableCell>
                          </TableRow>
                        )}
                      </TableBody>
                    </Table>
                  </div>
                </div>
              </>
            )}
          </CardContent>
        </Card>
    </TabsContent>
  )
}
