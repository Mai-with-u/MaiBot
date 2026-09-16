import { useMemo, useState } from 'react'

import { useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Check,
  Loader2,
  MoreHorizontal,
  PackageOpen,
  Plus,
  RefreshCw,
  Search,
  Upload,
} from 'lucide-react'

import { MemoryFactEditorDialog } from '@/components/memory/MemoryFactEditorDialog'
import { MemoryMiniTabs } from '@/components/memory/MemoryMiniTabs'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Checkbox } from '@/components/ui/checkbox'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Progress } from '@/components/ui/progress'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Textarea } from '@/components/ui/textarea'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import { useToast } from '@/hooks/use-toast'
import { formatChatAccountLabel, formatChatDisplayName } from '@/lib/chat-display'
import { cn } from '@/lib/utils'
import {
  createMemoryFact,
  type MemoryFactWritePayload,
  type MemoryImportChatTargetPayload,
  type MemoryImportTaskKind,
} from '@/lib/memory-api'

import {
  IMPORT_CONTENT_CATEGORY_OPTIONS,
  IMPORT_INPUT_MODE_OPTIONS,
  IMPORT_KIND_OPTIONS,
} from '../constants'
import {
  CONVERTED_IMPORT_ALIAS,
  LPMM_IMPORT_ALIAS,
  RAW_IMPORT_ALIAS,
} from '../hooks/useImportForm'
import type {
  ImportContentCategory,
  UnifiedImportMode,
  UseImportFormResult,
} from '../hooks/useImportForm'
import type { UseImportQueueResult } from '../hooks/useImportQueue'
import {
  formatImportTime,
  formatProgressPercent,
  getImportStatusLabel,
  getImportStatusVariant,
  getImportStepLabel,
  getImportTaskKindLabel,
  normalizeImportInputMode,
  normalizeProgress,
} from '../utils'
import { ImportTaskDetailDialog } from './ImportTaskDetailDialog'
import { MemoryBundleCard } from './MemoryBundleCard'

const UNIFIED_IMPORT_MODE_OPTIONS = [
  { value: 'text', label: '文本', description: '粘贴一段文本作为导入内容' },
  { value: 'file', label: '文件', description: '上传 txt / md / json 文件' },
  { value: 'folder', label: '文件夹', description: '扫描服务端目录下的文件' },
]

/** 触发框 + 悬停注释：选中后悬停即可回顾当前选项的说明 */
function SelectTriggerWithHint({
  hint,
  children,
  ...triggerProps
}: React.ComponentProps<typeof SelectTrigger> & { hint: string }) {
  return (
    <TooltipProvider delayDuration={150}>
      <Tooltip>
        <TooltipTrigger asChild>
          <SelectTrigger {...triggerProps}>{children}</SelectTrigger>
        </TooltipTrigger>
        {hint ? (
          <TooltipContent side="right" className="max-w-xs">{hint}</TooltipContent>
        ) : null}
      </Tooltip>
    </TooltipProvider>
  )
}

/** 路径输入框 + 内联「检查」按钮：预检的就是该模式提交时实际使用的别名与相对路径 */
function PathCheckField({
  label,
  value,
  onChange,
  alias,
  mustExist,
  checkPath,
}: {
  label: string
  value: string
  onChange: (value: string) => void
  alias: string
  /** 源路径要求已存在（避免扫空目录）；转换目标路径允许创建时不存在 */
  mustExist: boolean
  checkPath: (alias: string, relativePath: string, mustExist: boolean) => Promise<string>
}) {
  const [checking, setChecking] = useState(false)
  const [result, setResult] = useState('')

  return (
    <div className="space-y-1">
      <Label>{label}</Label>
      <div className="flex gap-2">
        <Input value={value} onChange={(event) => onChange(event.target.value)} />
        <Button
          type="button"
          variant="outline"
          disabled={checking}
          onClick={() => {
            void (async () => {
              setChecking(true)
              setResult(await checkPath(alias, value, mustExist))
              setChecking(false)
            })()
          }}
        >
          {checking ? '检查中…' : '检查'}
        </Button>
      </div>
      {result ? <div className="text-xs text-muted-foreground">{result}</div> : null}
    </div>
  )
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
    formatChatAccountLabel(chat.account_id),
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
    return '所有聊天可用'
  }
  const idLabel = chat.group_id || chat.user_id
  const displayName = formatChatDisplayName(chat.chat_name, chat.account_id)
  return idLabel ? `${displayName} · ${idLabel}` : displayName
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
    importErrorText,
  } = queue
  const {
    importCreateMode,
    setImportCreateMode,
    unifiedImportMode,
    setUnifiedImportMode,
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
    importContentCategory,
    setImportContentCategory,
    importContentCategoryMissing,
    importCommonDedupePolicy,
    setImportCommonDedupePolicy,
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
    rawInputMode,
    setRawInputMode,
    rawRelativePath,
    setRawRelativePath,
    rawGlob,
    setRawGlob,
    rawRecursive,
    setRawRecursive,
    openieRelativePath,
    setOpenieRelativePath,
    openieIncludeAllJson,
    setOpenieIncludeAllJson,
    convertRelativePath,
    setConvertRelativePath,
    convertTargetRelativePath,
    setConvertTargetRelativePath,
    convertDimension,
    setConvertDimension,
    convertBatchSize,
    setConvertBatchSize,
    submitImportByMode,
    creatingImport,
    checkImportPath,
  } = form
  const [chatTargetQuery, setChatTargetQuery] = useState('')
  const [importParametersOpen, setImportParametersOpen] = useState(false)
  const [transferMode, setTransferMode] = useState<'import' | 'bundle' | 'fact'>('import')
  const [taskDetailOpen, setTaskDetailOpen] = useState(false)
  const [factEditorOpen, setFactEditorOpen] = useState(false)

  // 点队列卡片即选中该任务并打开详情弹窗（详情不再常驻页面）
  const openTaskDetail = (taskId: string) => {
    setTaskDetailOpen(true)
    void selectImportTask(taskId)
  }
  const queryClient = useQueryClient()
  const { toast } = useToast()

  const createFactMutation = useMutation({
    mutationFn: async (payload: MemoryFactWritePayload) => {
      const response = await createMemoryFact(payload)
      if (!response.success) {
        throw new Error(response.error || '事实保存失败')
      }
      return response
    },
    onSuccess: async (payload) => {
      setFactEditorOpen(false)
      // 手动录入的事实要反映到「记忆查询」页，失效相关查询缓存。
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['memory-records'] }),
        queryClient.invalidateQueries({ queryKey: ['memory-record-context'] }),
      ])
      toast({
        title: payload.replaced ? '事实已修订' : '事实已保存',
        description: payload.refresh_queued ? '相关人物画像已进入刷新队列。' : undefined,
      })
    },
    onError: (error) => {
      toast({
        title: '保存事实失败',
        description: error instanceof Error ? error.message : String(error),
        variant: 'destructive',
      })
    },
  })

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
      <Tabs
        value={transferMode}
        onValueChange={(value) => setTransferMode(value as 'import' | 'bundle' | 'fact')}
      >
        <TabsList
          aria-label="长期记忆写入方式"
          className="border-border/60 bg-muted/30 h-auto w-fit gap-1 rounded-xl border"
        >
          <TabsTrigger value="import">
            <Upload className="mr-2 h-4 w-4" />
            导入任务
          </TabsTrigger>
          <TabsTrigger value="bundle">
            <PackageOpen className="mr-2 h-4 w-4" />
            记忆包导入导出
          </TabsTrigger>
          <TabsTrigger value="fact">
            <Plus className="mr-2 h-4 w-4" />
            新增事实
          </TabsTrigger>
        </TabsList>
      </Tabs>

      <div
        className={cn(
          'grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]',
          transferMode !== 'import' && 'hidden',
        )}
      >
        <div className="order-2 space-y-6 lg:order-1">
          <Card className="rounded-2xl border-border/70 shadow-sm">
            <CardHeader>
              <CardTitle className="flex items-center justify-between gap-2">
                <span className="flex items-center gap-2">
                  <Upload className="h-4 w-4" />
                  创建导入任务
                </span>
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button
                      type="button"
                      variant="outline"
                      size="icon"
                      className="shrink-0"
                      aria-label="切换导入方式"
                      title="切换导入方式"
                    >
                      <MoreHorizontal className="h-4 w-4" />
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end" className="w-60">
                    <DropdownMenuRadioGroup
                      value={importCreateMode}
                      onValueChange={(value) => setImportCreateMode(value as MemoryImportTaskKind)}
                    >
                      {IMPORT_KIND_OPTIONS.map((item) => (
                        <DropdownMenuRadioItem key={item.value} value={item.value} className="items-start">
                          <span className="flex flex-col">
                            <span>{item.label}</span>
                            <span className="text-xs text-muted-foreground">{item.description}</span>
                          </span>
                        </DropdownMenuRadioItem>
                      ))}
                    </DropdownMenuRadioGroup>
                  </DropdownMenuContent>
                </DropdownMenu>
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-6">
              <Tabs
                value={importCreateMode}
                onValueChange={(value) => setImportCreateMode(value as MemoryImportTaskKind)}
                className="space-y-4"
              >
                <div className="space-y-2">
                  <Label>
                    资料类别 <span className="text-destructive">*</span>
                  </Label>
                  <Select
                    value={importContentCategory}
                    onValueChange={(value) =>
                      setImportContentCategory(value as ImportContentCategory)
                    }
                  >
                    <SelectTriggerWithHint
                      aria-label="资料类别"
                      aria-required
                      aria-invalid={importContentCategoryMissing}
                      hint={
                        IMPORT_CONTENT_CATEGORY_OPTIONS.find(
                          (item) => item.value === importContentCategory
                        )?.description ?? ''
                      }
                    >
                      <SelectValue placeholder="请选择资料类别" />
                    </SelectTriggerWithHint>
                    <SelectContent>
                      {IMPORT_CONTENT_CATEGORY_OPTIONS.map((item) => (
                        <SelectItem key={item.value} value={item.value} title={item.description}>
                          {item.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  {importContentCategoryMissing ? (
                    <div className="text-xs text-destructive" role="status">
                      请选择资料类别
                    </div>
                  ) : null}
                </div>

                <Dialog open={importParametersOpen} onOpenChange={setImportParametersOpen}>
                  <DialogContent
                    aria-describedby={undefined}
                    className="max-h-[85vh] overflow-y-auto [--dialog-width:72rem]"
                  >
                    <DialogHeader>
                      <DialogTitle>导入参数</DialogTitle>
                    </DialogHeader>
                    <div className="space-y-4">
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
                  <div className="grid gap-3 rounded-md border bg-background/70 p-3 md:col-span-2 lg:grid-cols-[minmax(14rem,1fr)_minmax(18rem,28rem)]">
                    <div className="min-w-0">
                      <Label>资料范围</Label>
                      <div className="mt-0.5 text-xs text-muted-foreground">选择所有聊天可用，或将这批记忆限制在一个明确的聊天流内。</div>
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
                          <span className="truncate">所有聊天可用</span>
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
                  </DialogContent>
                </Dialog>

              <TabsContent value="upload" className="mt-0">
                <Tabs
                  value={unifiedImportMode}
                  onValueChange={(value) => setUnifiedImportMode(value as UnifiedImportMode)}
                  className="space-y-4"
                >
                  <MemoryMiniTabs items={UNIFIED_IMPORT_MODE_OPTIONS} />

                  <TabsContent value="text" className="mt-0 space-y-3">
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
                            {IMPORT_INPUT_MODE_OPTIONS.map((item) => (
                              <SelectItem key={item.value} value={item.value} title={item.description}>
                                {item.label}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      </div>
                      <div className="space-y-1">
                        <Label>文本内容</Label>
                        <Textarea
                          value={pasteContent}
                          onChange={(event) => setPasteContent(event.target.value)}
                          rows={8}
                        />
                      </div>
                    </div>
                  </TabsContent>

                  <TabsContent value="file" className="mt-0 space-y-3">
                    <div className="grid gap-3">
                      <div className="space-y-1">
                        <Label>输入模式</Label>
                        <Select
                          value={uploadInputMode}
                          onValueChange={(value) =>
                            setUploadInputMode(normalizeImportInputMode(value))
                          }
                        >
                          <SelectTrigger aria-label="upload-input-mode">
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            {IMPORT_INPUT_MODE_OPTIONS.map((item) => (
                              <SelectItem key={item.value} value={item.value} title={item.description}>
                                {item.label}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      </div>
                      <div className="space-y-1">
                        <Label>文件选择</Label>
                        <Input
                          type="file"
                          multiple
                          accept=".txt,.md,.json"
                          onChange={(event) =>
                            setUploadFiles(Array.from(event.target.files ?? []))
                          }
                        />
                      </div>
                    </div>
                    <div className="text-xs text-muted-foreground">
                      已选择 {uploadFiles.length} 个文件
                    </div>
                  </TabsContent>

                  <TabsContent value="folder" className="mt-0 space-y-3">
                    <div className="grid gap-3">
                      <div className="space-y-1">
                        <Label>输入模式</Label>
                        <Select
                          value={rawInputMode}
                          onValueChange={(value) =>
                            setRawInputMode(normalizeImportInputMode(value))
                          }
                        >
                          <SelectTrigger aria-label="raw-input-mode">
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            {IMPORT_INPUT_MODE_OPTIONS.map((item) => (
                              <SelectItem key={item.value} value={item.value} title={item.description}>
                                {item.label}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      </div>
                      <PathCheckField
                        label="相对路径"
                        value={rawRelativePath}
                        onChange={setRawRelativePath}
                        alias={RAW_IMPORT_ALIAS}
                        mustExist
                        checkPath={checkImportPath}
                      />
                      <div className="space-y-1">
                        <Label>匹配规则（Glob）</Label>
                        <Input value={rawGlob} onChange={(event) => setRawGlob(event.target.value)} />
                      </div>
                    </div>
                    <div className="flex items-center gap-2 text-sm">
                      <Checkbox
                        checked={rawRecursive}
                        onCheckedChange={(value) => setRawRecursive(Boolean(value))}
                      />
                      递归扫描
                    </div>
                  </TabsContent>
                </Tabs>
              </TabsContent>

              <TabsContent value="lpmm_openie" className="mt-0">
                <div className="space-y-3 rounded-xl border bg-background/70 p-4">
                  <div className="text-xs text-muted-foreground">读取 LPMM 内容并抽取关系</div>
                  <div className="grid gap-3">
                    <PathCheckField
                      label="相对路径"
                      value={openieRelativePath}
                      onChange={setOpenieRelativePath}
                      alias={LPMM_IMPORT_ALIAS}
                      mustExist
                      checkPath={checkImportPath}
                    />
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
                    <PathCheckField
                      label="源相对路径"
                      value={convertRelativePath}
                      onChange={setConvertRelativePath}
                      alias={LPMM_IMPORT_ALIAS}
                      mustExist
                      checkPath={checkImportPath}
                    />
                    <PathCheckField
                      label="目标相对路径"
                      value={convertTargetRelativePath}
                      onChange={setConvertTargetRelativePath}
                      alias={CONVERTED_IMPORT_ALIAS}
                      mustExist={false}
                      checkPath={checkImportPath}
                    />
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

              </Tabs>

              <div className="flex items-center gap-2">
                <Button
                  className="min-w-0 flex-1"
                  onClick={() => void submitImportByMode()}
                  disabled={creatingImport || importContentCategoryMissing}
                >
                  {creatingImport ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Upload className="mr-2 h-4 w-4" />}
                  创建导入任务
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  size="icon"
                  className="shrink-0"
                  aria-label="导入参数"
                  title="导入参数"
                  onClick={() => setImportParametersOpen(true)}
                >
                  <MoreHorizontal className="h-4 w-4" />
                </Button>
              </div>
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
            </CardHeader>
            <CardContent className="space-y-6">
              {importErrorText ? (
                <Alert variant="destructive">
                  <AlertDescription>{importErrorText}</AlertDescription>
                </Alert>
              ) : null}

              {runningImportTasks.length > 0 ? (
                <div className="space-y-2.5">
                  <div className="flex items-center justify-between gap-2">
                    <div className="text-sm font-medium">运行中</div>
                    <Badge variant="outline">{runningImportTasks.length}</Badge>
                  </div>
                  <ScrollArea className="h-[208px]">
                    <div className="space-y-2.5 p-2.5">
                      {runningImportTasks.map((task) => {
                        const isSelected = task.task_id === selectedImportTaskId
                        return (
                          <button
                            key={task.task_id}
                            type="button"
                            onClick={() => openTaskDetail(task.task_id)}
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
                                <div className="text-sm font-medium">{getImportTaskKindLabel(String(task.task_kind ?? task.mode ?? '-'))}</div>
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
                </div>
              ) : null}

              {queuedImportTasks.length > 0 ? (
                <div className="space-y-2.5">
                  <div className="flex items-center justify-between gap-2">
                    <div className="text-sm font-medium">排队中</div>
                    <Badge variant="outline">{queuedImportTasks.length}</Badge>
                  </div>
                  <ScrollArea className="h-[188px]">
                    <div className="space-y-2.5 p-2.5">
                      {queuedImportTasks.map((task) => {
                        const isSelected = task.task_id === selectedImportTaskId
                        return (
                          <button
                            key={task.task_id}
                            type="button"
                            onClick={() => openTaskDetail(task.task_id)}
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
                                <div className="text-sm font-medium">{getImportTaskKindLabel(String(task.task_kind ?? task.mode ?? '-'))}</div>
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
                </div>
              ) : null}

              {recentImportTasks.length > 0 ? (
                <div className="space-y-2.5">
                  <div className="flex items-center justify-between gap-2">
                    <div className="text-sm font-medium">最近完成</div>
                    <Badge variant="secondary">{recentImportTasks.length}</Badge>
                  </div>
                  <ScrollArea className="h-[260px]">
                    <div className="space-y-2.5 p-2.5">
                      {recentImportTasks.map((task) => {
                        const isSelected = task.task_id === selectedImportTaskId
                        return (
                          <button
                            key={task.task_id}
                            type="button"
                            onClick={() => openTaskDetail(task.task_id)}
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
                                <div className="text-sm font-medium">{getImportTaskKindLabel(String(task.task_kind ?? task.mode ?? '-'))}</div>
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
                </div>
              ) : null}
            </CardContent>
          </Card>
        </div>
      </div>

      {transferMode === 'import' ? (
        <ImportTaskDetailDialog
          open={taskDetailOpen}
          onOpenChange={setTaskDetailOpen}
          queue={queue}
        />
      ) : null}

      {transferMode === 'bundle' ? <MemoryBundleCard chatTargets={importChatTargets} /> : null}
      {transferMode === 'fact' ? (
        <div className="max-w-2xl">
          <Card className="rounded-2xl border-border/70 bg-card/85 shadow-sm">
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Plus className="h-4 w-4" />
                新增事实
              </CardTitle>
              <CardDescription>手动录入一条结构化事实，直接写入长期记忆事实账本。</CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              <Button type="button" onClick={() => setFactEditorOpen(true)}>
                <Plus className="mr-2 h-4 w-4" />
                新增事实
              </Button>
              <p className="text-xs text-muted-foreground">
                事实按「主体 - 谓词 - 客体」成条写入，录入后可在「记忆查询」页继续编辑、撤回或恢复。
              </p>
            </CardContent>
          </Card>
        </div>
      ) : null}
      <MemoryFactEditorDialog
        open={factEditorOpen}
        onOpenChange={setFactEditorOpen}
        record={null}
        saving={createFactMutation.isPending}
        onSubmit={(payload) => createFactMutation.mutate(payload)}
      />
    </TabsContent>
  )
}
