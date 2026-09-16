import { RotateCcw, Trash2, X } from 'lucide-react'

import { MemoryMiniTabs } from '@/components/memory/MemoryMiniTabs'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Tabs, TabsContent } from '@/components/ui/tabs'
import { ThinkingIllustration } from '@/components/ui/thinking-illustration'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import { cn } from '@/lib/utils'

import {
  DELETE_OPERATION_ITEM_PAGE_SIZE,
  DELETE_OPERATION_PAGE_SIZE,
  MEMORY_SOURCE_KIND_FILTER_ALL,
  MEMORY_SOURCE_KIND_FILTER_KINDS,
  MEMORY_SOURCE_KIND_FILTER_OTHER,
} from '../constants'
import type { UseMemoryDeleteResult } from '../hooks/useMemoryDelete'
import {
  describeMemorySource,
  formatDeleteOperationMode,
  formatDeleteOperationStatus,
  formatDeleteOperationTime,
  getDeleteOperationItemLabel,
  getDeleteOperationItemPreview,
  getDeleteOperationItemSource,
  getDeleteOperationStatusGroup,
  getMemorySourceKindLabel,
  summarizeDeleteSelector,
} from '../utils'

/** 来源类别筛选标签：全部 + 有独立标签页的类别 + 收敛其余来源的「其他」 */
const SOURCE_KIND_FILTER_OPTIONS: Array<{ value: string; label: string }> = [
  { value: MEMORY_SOURCE_KIND_FILTER_ALL, label: '全部' },
  ...MEMORY_SOURCE_KIND_FILTER_KINDS.map((kind) => ({
    value: kind,
    label: getMemorySourceKindLabel(kind),
  })),
  { value: MEMORY_SOURCE_KIND_FILTER_OTHER, label: '其他' },
]

export interface DeleteTabProps {
  delete: UseMemoryDeleteResult
}

export function DeleteTab({ delete: memoryDelete }: DeleteTabProps) {
  const {
    sourceKindFilter,
    setSourceKindFilter,
    sourceSearch,
    setSourceSearch,
    selectedSources,
    setSelectedSources,
    filteredSources,
    openSourceDeletePreview,
    toggleSourceSelection,
    sourceNameBySource,
    operationSearch,
    setOperationSearch,
    operationModeFilter,
    setOperationModeFilter,
    operationStatusFilter,
    setOperationStatusFilter,
    filteredDeleteOperations,
    deleteOperations,
    operationPage,
    setOperationPage,
    deleteOperationPageCount,
    pagedDeleteOperations,
    selectedDeleteOperation,
    setSelectedOperationId,
    restoreDeleteOperation,
    deleteRestoring,
    selectedOperationCounts,
    selectedOperationDetailLoading,
    selectedOperationDetailError,
    selectedOperationSources,
    selectedOperationItems,
    filteredSelectedOperationItems,
    selectedOperationItemSearch,
    setSelectedOperationItemSearch,
    selectedOperationItemPage,
    setSelectedOperationItemPage,
    selectedOperationItemPageCount,
    pagedSelectedOperationItems,
  } = memoryDelete

  const selectorSummary = summarizeDeleteSelector(selectedDeleteOperation?.selector)
  const selectedStatusGroup = getDeleteOperationStatusGroup(
    String(selectedDeleteOperation?.status ?? '')
  )
  // 切到具体某个类别时，行尾再挂一枚同类徽章没有信息量；「全部」「其他」下才需要它区分来源
  const showSourceKindBadge =
    sourceKindFilter === MEMORY_SOURCE_KIND_FILTER_ALL ||
    sourceKindFilter === MEMORY_SOURCE_KIND_FILTER_OTHER

  return (
    <TabsContent value="delete" className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Trash2 className="h-4 w-4" />
            来源批量删除
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-3 rounded-xl border bg-muted/20 p-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <Tabs value={sourceKindFilter} onValueChange={setSourceKindFilter} className="min-w-0">
                <MemoryMiniTabs items={SOURCE_KIND_FILTER_OPTIONS} />
              </Tabs>
              <Button
                variant="outline"
                onClick={() => setSelectedSources(filteredSources.map((item) => String(item.source ?? '')).filter(Boolean))}
              >
                全选当前结果
              </Button>
            </div>
            {sourceSearch.trim() ? (
              <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                <span>已按来源限定：</span>
                <Badge variant="secondary" className="max-w-full break-all">{sourceSearch}</Badge>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={() => setSourceSearch('')}
                >
                  <X className="mr-1 h-3.5 w-3.5" />
                  清除限定
                </Button>
              </div>
            ) : null}
            <div className="flex flex-wrap items-center justify-between gap-2 border-t pt-3">
              <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
                <Badge variant="outline" className="bg-background/70">当前命中 {filteredSources.length} 个来源</Badge>
                <Badge variant={selectedSources.length > 0 ? 'secondary' : 'outline'} className="bg-background/70">
                  已选择 {selectedSources.length} 个来源
                </Badge>
                <span className="text-xs">选中的来源会一起删除</span>
              </div>
              <Button
                onClick={() => void openSourceDeletePreview()}
                disabled={selectedSources.length <= 0}
              >
                <Trash2 className="mr-2 h-4 w-4" />
                预览删除
              </Button>
            </div>
          </div>

          <TooltipProvider delayDuration={150}>
            <ScrollArea className="h-[320px] rounded-lg border">
              <Table>
                <TableHeader className="sticky top-0 bg-background">
                  <TableRow>
                    <TableHead className="w-16">选中</TableHead>
                    <TableHead>来源</TableHead>
                    <TableHead className="w-24 text-right">段落数</TableHead>
                    <TableHead className="w-40">最后更新</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {filteredSources.length > 0 ? filteredSources.map((item) => {
                    const source = String(item.source ?? '')
                    const checked = selectedSources.includes(source)
                    const display = describeMemorySource(source, {
                      sourceKind: String(item.source_kind ?? ''),
                      chatName: String(item.chat_name ?? ''),
                      personName: String(item.person_name ?? ''),
                    })
                    return (
                      <TableRow key={source}>
                        <TableCell>
                          <Checkbox checked={checked} onCheckedChange={(value) => toggleSourceSelection(source, Boolean(value))} />
                        </TableCell>
                        <TableCell>
                          <Tooltip>
                            <TooltipTrigger asChild>
                              <div className="space-y-1">
                                <div className="flex flex-wrap items-center gap-2">
                                  <span className="text-sm font-medium break-all">{display.title || '—'}</span>
                                  {showSourceKindBadge && display.kindLabel ? (
                                    <Badge variant="secondary" className="shrink-0">{display.kindLabel}</Badge>
                                  ) : null}
                                </div>
                                {display.raw ? (
                                  <code className="block text-[11px] break-all text-muted-foreground">{display.raw}</code>
                                ) : null}
                              </div>
                            </TooltipTrigger>
                            <TooltipContent side="right" className="max-w-sm space-y-1">
                              <div>类别：{display.kindLabel || '未识别'}</div>
                              <div className="break-all">原始来源：{source}</div>
                              {item.chat_id ? <div className="break-all">聊天流 ID：{String(item.chat_id)}</div> : null}
                              {item.person_id ? <div className="break-all">人物 ID：{String(item.person_id)}</div> : null}
                              <div>段落数：{Number(item.paragraph_count ?? 0)}</div>
                              <div>最后更新：{formatDeleteOperationTime(item.last_updated)}</div>
                            </TooltipContent>
                          </Tooltip>
                        </TableCell>
                        <TableCell className="text-right">{Number(item.paragraph_count ?? 0)}</TableCell>
                        <TableCell className="text-xs text-muted-foreground">
                          {formatDeleteOperationTime(item.last_updated)}
                        </TableCell>
                      </TableRow>
                    )
                  }) : (
                    <TableRow>
                      <TableCell colSpan={4} className="text-center text-muted-foreground">
                        当前没有可删除的来源
                      </TableCell>
                    </TableRow>
                  )}
                </TableBody>
              </Table>
            </ScrollArea>
          </TooltipProvider>
          <p className="text-xs text-muted-foreground">
            表格的段落数来自来源统计。删除会连带清理失去全部证据的关系，实际影响面在预览里确认。
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <RotateCcw className="h-4 w-4" />
            删除操作恢复
          </CardTitle>
          <CardDescription>按列表浏览最近的删除操作，先选中记录，再在下方确认影响范围并执行恢复</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-3 rounded-xl border bg-muted/20 p-4 lg:grid-cols-[minmax(0,1fr)_180px_180px]">
            <Input
              value={operationSearch}
              onChange={(event) => setOperationSearch(event.target.value)}
              placeholder="搜索 operation / reason / requested_by / source"
            />
            <Select value={operationModeFilter} onValueChange={setOperationModeFilter}>
              <SelectTrigger>
                <SelectValue placeholder="按模式筛选" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">全部模式</SelectItem>
                <SelectItem value="source">来源删除</SelectItem>
                <SelectItem value="mixed">混合删除</SelectItem>
                <SelectItem value="entity">实体删除</SelectItem>
                <SelectItem value="relation">关系删除</SelectItem>
                <SelectItem value="paragraph">段落删除</SelectItem>
              </SelectContent>
            </Select>
            <Select value={operationStatusFilter} onValueChange={setOperationStatusFilter}>
              <SelectTrigger>
                <SelectValue placeholder="按状态筛选" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">全部状态</SelectItem>
                <SelectItem value="executed">已执行</SelectItem>
                <SelectItem value="restored">已恢复</SelectItem>
              </SelectContent>
            </Select>
          </div>

          <div className="flex flex-wrap items-center justify-between gap-2 text-sm text-muted-foreground">
            <span>当前命中 {filteredDeleteOperations.length} 条记录，已加载最近 {deleteOperations.length} 条</span>
            <span>第 {operationPage} / {deleteOperationPageCount} 页，每页显示 {DELETE_OPERATION_PAGE_SIZE} 条</span>
          </div>

          <ScrollArea className="h-[320px] rounded-lg border">
            <div className="space-y-3 p-3">
              {pagedDeleteOperations.length > 0 ? pagedDeleteOperations.map((operation) => {
                const summary = (operation.summary ?? {}) as Record<string, unknown>
                const counts = ((summary.counts as Record<string, number> | undefined) ?? {})
                const isSelected = selectedDeleteOperation?.operation_id === operation.operation_id
                return (
                  <button
                    key={operation.operation_id}
                    type="button"
                    onClick={() => setSelectedOperationId(operation.operation_id)}
                    className={cn(
                      'w-full rounded-xl border p-4 text-left transition-colors',
                      isSelected
                        ? 'border-primary bg-primary/5 shadow-sm'
                        : 'bg-muted/20 hover:border-primary/40 hover:bg-muted/40',
                    )}
                  >
                    <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
                      <div className="min-w-0 space-y-2">
                        <div className="flex flex-wrap items-center gap-2">
                          <Badge
                            variant={
                              getDeleteOperationStatusGroup(String(operation.status ?? '')) === 'applied'
                                ? 'default'
                                : 'secondary'
                            }
                          >
                            {formatDeleteOperationStatus(String(operation.status ?? ''))}
                          </Badge>
                          <Badge variant="outline">
                            {formatDeleteOperationMode(String(operation.mode ?? ''))}
                          </Badge>
                        </div>
                        <div className="font-mono text-xs break-all">{operation.operation_id}</div>
                        <div className="text-sm text-muted-foreground">
                          {operation.reason || '未填写原因'}
                        </div>
                      </div>
                      <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground lg:max-w-[280px] lg:justify-end">
                        <span className="text-muted-foreground/70">删除内容</span>
                        <span>实体 {Number(counts.entities ?? 0)}</span>
                        <span>关系 {Number(counts.relations ?? 0)}</span>
                        <span>段落 {Number(counts.paragraphs ?? 0)}</span>
                        <span>来源 {Number(counts.sources ?? 0)}</span>
                      </div>
                    </div>
                    <div className="mt-3 text-xs text-muted-foreground">
                      {formatDeleteOperationTime(operation.created_at)}
                    </div>
                  </button>
                )
              }) : (
                <div className="rounded-lg border border-dashed bg-muted/20 p-6 text-center text-sm text-muted-foreground">
                  当前筛选条件下没有删除操作
                </div>
              )}
            </div>
          </ScrollArea>

          <div className="flex items-center justify-between gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => setOperationPage((current) => Math.max(1, current - 1))}
              disabled={operationPage <= 1}
            >
              上一页
            </Button>
            <div className="text-xs text-muted-foreground">
              支持按删除记录、模式、状态、发起人和来源检索
            </div>
            <Button
              variant="outline"
              size="sm"
              onClick={() => setOperationPage((current) => Math.min(deleteOperationPageCount, current + 1))}
              disabled={operationPage >= deleteOperationPageCount}
            >
              下一页
            </Button>
          </div>

          <div className="rounded-xl border bg-muted/20 p-4">
            {selectedDeleteOperation ? (
              <div className="space-y-4">
                <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
                  <div className="space-y-2">
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge variant={selectedStatusGroup === 'applied' ? 'default' : 'secondary'}>
                        {formatDeleteOperationStatus(String(selectedDeleteOperation.status ?? ''))}
                      </Badge>
                      <Badge variant="outline">
                        {formatDeleteOperationMode(String(selectedDeleteOperation.mode ?? ''))}
                      </Badge>
                    </div>
                    <div className="font-mono text-xs break-all">{selectedDeleteOperation.operation_id}</div>
                    <div className="text-sm text-muted-foreground">
                      {selectedDeleteOperation.reason || '未填写删除原因'}
                    </div>
                  </div>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => void restoreDeleteOperation(selectedDeleteOperation.operation_id)}
                    disabled={
                      selectedStatusGroup === 'restored' ||
                      selectedStatusGroup === 'restoring' ||
                      deleteRestoring
                    }
                  >
                    <RotateCcw className="mr-2 h-4 w-4" />
                    {selectedStatusGroup === 'restored'
                      ? '已恢复'
                      : selectedStatusGroup === 'restoring'
                        ? '恢复中'
                        : '恢复这次删除'}
                  </Button>
                </div>

                <div className="grid gap-3 lg:grid-cols-4">
                  <div className="rounded-lg border bg-background/60 p-3">
                    <div className="text-xs text-muted-foreground">发起人</div>
                    <div className="mt-1 text-sm">{selectedDeleteOperation.requested_by || '-'}</div>
                  </div>
                  <div className="rounded-lg border bg-background/60 p-3">
                    <div className="text-xs text-muted-foreground">创建时间</div>
                    <div className="mt-1 text-sm">{formatDeleteOperationTime(selectedDeleteOperation.created_at)}</div>
                  </div>
                  <div className="rounded-lg border bg-background/60 p-3">
                    <div className="text-xs text-muted-foreground">恢复时间</div>
                    <div className="mt-1 text-sm">{formatDeleteOperationTime(selectedDeleteOperation.restored_at)}</div>
                  </div>
                  <div className="rounded-lg border bg-background/60 p-3">
                    <div className="text-xs text-muted-foreground">删除摘要</div>
                    <div className="mt-1 flex flex-wrap gap-2">
                      <Badge variant="outline">实体 {Number(selectedOperationCounts.entities ?? 0)}</Badge>
                      <Badge variant="outline">关系 {Number(selectedOperationCounts.relations ?? 0)}</Badge>
                      <Badge variant="outline">段落 {Number(selectedOperationCounts.paragraphs ?? 0)}</Badge>
                      <Badge variant="outline">来源 {Number(selectedOperationCounts.sources ?? 0)}</Badge>
                    </div>
                  </div>
                </div>

                {selectedOperationDetailLoading ? (
                  <div className="rounded-lg border bg-background/60 p-4">
                    <ThinkingIllustration size="sm" />
                  </div>
                ) : null}

                {selectedOperationDetailError ? (
                  <Alert variant="destructive">
                    <AlertDescription>{selectedOperationDetailError}</AlertDescription>
                  </Alert>
                ) : null}

                {selectedOperationSources.length > 0 ? (
                  <div className="space-y-2">
                    <div className="text-sm font-semibold">关联来源</div>
                    <div className="flex flex-wrap gap-2">
                      {selectedOperationSources.map((source) => {
                        const display = describeMemorySource(source, {
                          chatName: sourceNameBySource[source],
                        })
                        return (
                          <Badge key={source} variant="secondary" className="max-w-full break-all">
                            {display.title}
                            {display.raw ? (
                              <span className="ml-1 font-mono text-[11px] text-muted-foreground">
                                {display.raw}
                              </span>
                            ) : null}
                          </Badge>
                        )
                      })}
                    </div>
                  </div>
                ) : null}

                <div className="grid gap-4 xl:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
                  <div className="space-y-2">
                    <div className="text-sm font-semibold">删除范围</div>
                    {selectorSummary.length > 0 ? (
                      <ul className="space-y-1 text-sm text-muted-foreground">
                        {selectorSummary.map((line) => (
                          <li key={line} className="break-all">{line}</li>
                        ))}
                      </ul>
                    ) : selectedOperationDetailLoading ? (
                      <p className="text-sm text-muted-foreground">正在加载这次删除的范围…</p>
                    ) : (
                      <p className="text-sm text-muted-foreground">这次删除没有记录可读范围。</p>
                    )}
                    <details className="rounded-lg border bg-background/70">
                      <summary className="cursor-pointer px-3 py-2 text-xs text-muted-foreground">
                        查看原始选择器
                      </summary>
                      <pre className="max-h-56 overflow-auto border-t p-3 text-xs break-words whitespace-pre-wrap">
                        {JSON.stringify(selectedDeleteOperation.selector ?? {}, null, 2)}
                      </pre>
                    </details>
                  </div>

                  <div className="space-y-2">
                    <div className="flex items-center justify-between">
                      <div className="text-sm font-semibold">影响对象</div>
                      <div className="text-xs text-muted-foreground">
                        命中 {filteredSelectedOperationItems.length} / {selectedOperationItems.length} 项
                      </div>
                    </div>
                    <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
                      <Input
                        value={selectedOperationItemSearch}
                        onChange={(event) => setSelectedOperationItemSearch(event.target.value)}
                        placeholder="搜索对象类型 / 哈希 / 对象键 / 来源"
                        className="lg:max-w-sm"
                      />
                      <div className="flex items-center justify-between gap-2 text-xs text-muted-foreground lg:min-w-[180px] lg:justify-end">
                        <span>第 {selectedOperationItemPage} / {selectedOperationItemPageCount} 页</span>
                        <span>每页 {DELETE_OPERATION_ITEM_PAGE_SIZE} 项</span>
                      </div>
                    </div>
                    <ScrollArea className="h-[280px] rounded-lg border bg-background/60">
                      <div className="space-y-2 p-3">
                        {pagedSelectedOperationItems.length > 0 ? pagedSelectedOperationItems.map((item) => {
                          const source = getDeleteOperationItemSource(item)
                          const label = getDeleteOperationItemLabel(item)
                          const preview = getDeleteOperationItemPreview(item)
                          return (
                            <div key={`${item.item_type}:${item.item_hash}:${item.item_key ?? ''}`} className="rounded-lg border bg-muted/20 p-3">
                              <div className="flex flex-wrap items-center gap-2">
                                <Badge variant="outline">{item.item_type}</Badge>
                                {source ? <Badge variant="secondary">{source}</Badge> : null}
                                {item.item_key && item.item_key !== item.item_hash ? (
                                  <span className="text-xs text-muted-foreground break-all">{item.item_key}</span>
                                ) : null}
                              </div>
                              <div className="mt-2 text-sm font-medium break-words">
                                {label}
                              </div>
                              {preview ? (
                                <div className="mt-1 text-xs text-muted-foreground break-words">
                                  {preview}
                                </div>
                              ) : null}
                              <div className="mt-2 font-mono text-[11px] break-all text-muted-foreground">
                                {item.item_hash}
                              </div>
                            </div>
                          )
                        }) : (
                          <div className="rounded-lg border border-dashed bg-muted/20 p-6 text-center text-sm text-muted-foreground">
                            {selectedOperationItems.length > 0 ? '当前筛选条件下没有明细项' : '当前操作没有记录明细项'}
                          </div>
                        )}
                      </div>
                    </ScrollArea>
                    <div className="flex items-center justify-between gap-2">
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => setSelectedOperationItemPage((current) => Math.max(1, current - 1))}
                        disabled={selectedOperationItemPage <= 1}
                      >
                        上一页
                      </Button>
                      <div className="text-xs text-muted-foreground">
                        支持按对象类型、哈希、对象键和来源检索
                      </div>
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => setSelectedOperationItemPage((current) => Math.min(selectedOperationItemPageCount, current + 1))}
                        disabled={selectedOperationItemPage >= selectedOperationItemPageCount}
                      >
                        下一页
                      </Button>
                    </div>
                  </div>
                </div>
              </div>
            ) : (
              <div className="flex min-h-[320px] items-center justify-center rounded-lg border border-dashed bg-background/40 p-6 text-center text-sm text-muted-foreground">
                当前没有可查看的删除操作详情
              </div>
            )}
          </div>
        </CardContent>
      </Card>
    </TabsContent>
  )
}
