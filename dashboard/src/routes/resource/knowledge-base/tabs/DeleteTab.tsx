import { CircleAlert, RotateCcw, Trash2 } from 'lucide-react'

import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { TabsContent } from '@/components/ui/tabs'
import { ThinkingIllustration } from '@/components/ui/thinking-illustration'
import { cn } from '@/lib/utils'

import { DELETE_OPERATION_ITEM_PAGE_SIZE, DELETE_OPERATION_PAGE_SIZE } from '../constants'
import type { UseMemoryDeleteResult } from '../hooks/useMemoryDelete'
import {
  formatDeleteOperationMode,
  formatDeleteOperationStatus,
  formatDeleteOperationTime,
  getDeleteOperationItemLabel,
  getDeleteOperationItemPreview,
  getDeleteOperationItemSource,
} from '../utils'

export interface DeleteTabProps {
  delete: UseMemoryDeleteResult
}

export function DeleteTab({ delete: memoryDelete }: DeleteTabProps) {
  const {
    sourceSearch,
    setSourceSearch,
    selectedSources,
    setSelectedSources,
    filteredSources,
    openSourceDeletePreview,
    toggleSourceSelection,
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

  return (
    <TabsContent value="delete" className="space-y-4">
      <div className="flex flex-col gap-4">
        <Card className="order-2">
          <CardHeader className="space-y-3">
            <div>
              <CardTitle className="flex items-center gap-2">
                <Trash2 className="h-4 w-4" />
                来源批量删除
              </CardTitle>
              <CardDescription>
                用于按来源清理测试数据或指定导入批次。该操作不会直接删除实体，只会删除来源段落和失去全部证据的关系。
              </CardDescription>
            </div>
            <Alert className="border-amber-500/30 bg-amber-500/5 text-amber-950 dark:text-amber-200">
              <CircleAlert className="h-4 w-4 text-amber-500" />
              <AlertDescription>
                建议先在图谱里确认影响范围，再在这里执行批量来源删除。所有删除都会先经过预览，并支持按删除记录恢复。
              </AlertDescription>
            </Alert>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid gap-3 rounded-xl border bg-muted/20 p-4 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-center">
              <div className="space-y-2">
                <Label>来源检索</Label>
                <Input
                  value={sourceSearch}
                  onChange={(event) => setSourceSearch(event.target.value)}
                  placeholder="搜索 source 名称"
                />
              </div>
              <div className="flex flex-wrap gap-2 lg:justify-end">
                <Button
                  variant="outline"
                  onClick={() => setSelectedSources(filteredSources.map((item) => String(item.source ?? '')).filter(Boolean))}
                >
                  全选当前结果
                </Button>
                <Button onClick={() => void openSourceDeletePreview()} disabled={selectedSources.length <= 0}>
                  <Trash2 className="mr-2 h-4 w-4" />
                  预览删除
                </Button>
              </div>
            </div>

            <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
              <Badge variant="outline" className="bg-background/70">当前命中 {filteredSources.length} 个来源</Badge>
              <Badge variant={selectedSources.length > 0 ? 'secondary' : 'outline'} className="bg-background/70">
                已选择 {selectedSources.length} 个来源
              </Badge>
            </div>

            <ScrollArea className="h-[320px] rounded-lg border">
              <Table>
                <TableHeader className="sticky top-0 bg-background">
                  <TableRow>
                    <TableHead className="w-12">选中</TableHead>
                    <TableHead>来源</TableHead>
                    <TableHead>段落数</TableHead>
                    <TableHead>关系数</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {filteredSources.length > 0 ? filteredSources.map((item) => {
                    const source = String(item.source ?? '')
                    const checked = selectedSources.includes(source)
                    return (
                      <TableRow key={source}>
                        <TableCell>
                          <Checkbox checked={checked} onCheckedChange={(value) => toggleSourceSelection(source, Boolean(value))} />
                        </TableCell>
                        <TableCell className="font-mono text-xs break-all">{source}</TableCell>
                        <TableCell>{Number(item.paragraph_count ?? 0)}</TableCell>
                        <TableCell>{Number(item.relation_count ?? 0)}</TableCell>
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
          </CardContent>
        </Card>

        <Card className="order-1">
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
                            <Badge variant={operation.status === 'restored' ? 'secondary' : 'default'}>
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
                        <div className="flex flex-wrap gap-2 text-xs text-muted-foreground lg:max-w-[280px] lg:justify-end">
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
                        <Badge variant={selectedDeleteOperation.status === 'restored' ? 'secondary' : 'default'}>
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
                      disabled={selectedDeleteOperation.status === 'restored' || deleteRestoring}
                    >
                      <RotateCcw className="mr-2 h-4 w-4" />
                      {selectedDeleteOperation.status === 'restored' ? '已恢复' : '恢复这次删除'}
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
                        {selectedOperationSources.map((source) => (
                          <Badge key={source} variant="secondary" className="max-w-full break-all">
                            {source}
                          </Badge>
                        ))}
                      </div>
                    </div>
                  ) : null}

                  <div className="grid gap-4 xl:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
                    <div className="space-y-2">
                      <div className="text-sm font-semibold">选择器</div>
                      <pre className="max-h-56 overflow-auto rounded-lg border bg-background/70 p-3 text-xs break-words whitespace-pre-wrap">
                        {JSON.stringify(selectedDeleteOperation.selector ?? {}, null, 2)}
                      </pre>
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
      </div>
    </TabsContent>
  )
}
