import { useState } from 'react'

import { RotateCcw, Trash2, X } from 'lucide-react'

import { MemoryMiniTabs } from '@/components/memory/MemoryMiniTabs'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Checkbox } from '@/components/ui/checkbox'
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
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

/** 来源类别筛选标签：有独立标签页的类别 + 收敛其余来源的「其他」 */
const SOURCE_KIND_FILTER_OPTIONS: Array<{ value: string; label: string }> = [
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
  // 切到具体某个类别时，行尾再挂一枚同类徽章没有信息量；「其他」下类别不固定才需要它区分来源
  const showSourceKindBadge = sourceKindFilter === MEMORY_SOURCE_KIND_FILTER_OTHER
  // 删除操作详情以弹出卡片展示；深链接带入初始操作时直接打开
  const [operationDetailOpen, setOperationDetailOpen] = useState(
    () => Boolean(memoryDelete.selectedOperationId)
  )

  const openOperationDetail = (operationId: string) => {
    setSelectedOperationId(operationId)
    setOperationDetailOpen(true)
  }

  return (
    <TabsContent value="delete" className="space-y-4">
      {/* 来源删除与删除记录恢复左右并排，窄屏时自动堆叠 */}
      <div className="grid min-w-0 gap-4 xl:grid-cols-2 xl:items-start">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Trash2 className="h-4 w-4" />
            按来源抹除
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="flex flex-wrap items-center gap-2 min-w-0">
                <Tabs value={sourceKindFilter} onValueChange={setSourceKindFilter} className="min-w-0">
                  <MemoryMiniTabs items={SOURCE_KIND_FILTER_OPTIONS} />
                </Tabs>
                <span className="text-xs text-muted-foreground">
                  {filteredSources.length} 个来源 · 已选 {selectedSources.length}
                </span>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <Button
                  variant="outline"
                  onClick={() => setSelectedSources(filteredSources.map((item) => String(item.source ?? '')).filter(Boolean))}
                >
                  全选当前结果
                </Button>
                <Button
                  onClick={() => void openSourceDeletePreview()}
                  disabled={selectedSources.length <= 0}
                >
                  <Trash2 className="h-4 w-4" />
                  预览删除
                </Button>
              </div>
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
          </div>

          <TooltipProvider delayDuration={150}>
            <ScrollArea data-memory-delete-screen="true" className="h-[360px] rounded-lg border bg-background">
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
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <RotateCcw className="h-4 w-4" />
            删除记录
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-2 2xl:grid-cols-[minmax(0,1fr)_8rem_8rem]">
            <Input
              value={operationSearch}
              onChange={(event) => setOperationSearch(event.target.value)}
              placeholder="搜索记录、原因、发起人或来源"
              aria-label="搜索删除记录"
              className="sm:col-span-2 2xl:col-span-1"
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

          <ScrollArea data-memory-delete-screen="true" className="h-[360px] rounded-lg border bg-background">
            <div className="divide-y">
              {pagedDeleteOperations.length > 0 ? pagedDeleteOperations.map((operation) => {
                const summary = (operation.summary ?? {}) as Record<string, unknown>
                const counts = ((summary.counts as Record<string, number> | undefined) ?? {})
                const isSelected = selectedDeleteOperation?.operation_id === operation.operation_id
                return (
                  <button
                    key={operation.operation_id}
                    type="button"
                    data-memory-delete-operation="true"
                    data-selected={isSelected}
                    onClick={() => openOperationDetail(operation.operation_id)}
                    className={cn(
                      'w-full p-4 text-left transition-colors hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring',
                      isSelected && 'bg-primary/5',
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
                          <span className="text-xs text-muted-foreground">
                            {formatDeleteOperationTime(operation.created_at)}
                          </span>
                        </div>
                        <div className="text-sm break-words">
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
                  </button>
                )
              }) : (
                <div className="p-6 text-center text-sm text-muted-foreground">
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
            <Button
              variant="outline"
              size="sm"
              onClick={() => setOperationPage((current) => Math.min(deleteOperationPageCount, current + 1))}
              disabled={operationPage >= deleteOperationPageCount}
            >
              下一页
            </Button>
          </div>
        </CardContent>
      </Card>
      </div>

      {/* 删除操作详情弹出卡片 */}
      <Dialog open={operationDetailOpen} onOpenChange={setOperationDetailOpen}>
        <DialogContent
          className="sm:[--dialog-width:64rem]"
          data-tour="operation-detail-dialog"
          aria-describedby={undefined}
        >
          <DialogHeader>
            <DialogTitle>删除操作详情</DialogTitle>
            <DialogDescription className="font-mono text-xs break-all">
              {selectedDeleteOperation?.operation_id ?? ''}
            </DialogDescription>
          </DialogHeader>

          <DialogBody viewportClassName="min-h-0 flex-1 pr-3 sm:pr-4 [&>div]:!block">
            {selectedDeleteOperation ? (
              <div className="space-y-4">
                <div className="space-y-2">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge variant={selectedStatusGroup === 'applied' ? 'default' : 'secondary'}>
                      {formatDeleteOperationStatus(String(selectedDeleteOperation.status ?? ''))}
                    </Badge>
                    <Badge variant="outline">
                      {formatDeleteOperationMode(String(selectedDeleteOperation.mode ?? ''))}
                    </Badge>
                  </div>
                  <div className="text-sm text-muted-foreground">
                    {selectedDeleteOperation.reason || '未填写删除原因'}
                  </div>
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
            ) : null}
          </DialogBody>

          <DialogFooter className="flex-row justify-end gap-2 space-x-0">
            {selectedDeleteOperation ? (
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
            ) : null}
            <Button size="sm" variant="outline" onClick={() => setOperationDetailOpen(false)}>
              关闭
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </TabsContent>
  )
}
