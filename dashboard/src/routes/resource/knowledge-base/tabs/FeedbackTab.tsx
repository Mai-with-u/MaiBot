import { RotateCcw } from 'lucide-react'

import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { TabsContent } from '@/components/ui/tabs'
import { ThinkingIllustration } from '@/components/ui/thinking-illustration'
import { cn } from '@/lib/utils'
import type { MemoryFeedbackActionLogPayload } from '@/lib/memory-api'

import { FEEDBACK_ACTION_LOG_PAGE_SIZE, FEEDBACK_CORRECTION_PAGE_SIZE } from '../constants'
import type { UseMemoryFeedbackResult } from '../hooks/useMemoryFeedback'
import {
  buildFeedbackImpactSummary,
  describeFeedbackActionLog,
  formatDeleteOperationTime,
  formatFeedbackActionType,
  formatFeedbackDecision,
  formatFeedbackRollbackStatus,
  formatFeedbackTaskStatus,
  getFeedbackCorrectionPreview,
  getFeedbackStatusVariant,
  summarizeFeedbackActionPayload,
} from '../utils'

export interface FeedbackTabProps {
  feedback: UseMemoryFeedbackResult
}

export function FeedbackTab({ feedback }: FeedbackTabProps) {
  const {
    feedbackSearch,
    setFeedbackSearch,
    feedbackStatusFilter,
    setFeedbackStatusFilter,
    feedbackRollbackFilter,
    setFeedbackRollbackFilter,
    filteredFeedbackCorrections,
    feedbackCorrections,
    pagedFeedbackCorrections,
    feedbackPage,
    setFeedbackPage,
    feedbackPageCount,
    selectedFeedbackCorrection,
    setSelectedFeedbackTaskId,
    selectedFeedbackResolved,
    selectedFeedbackPreview,
    selectedFeedbackImpactSummary,
    openFeedbackRollbackDialog,
    feedbackRollingBack,
    selectedFeedbackTaskLoading,
    selectedFeedbackTaskError,
    feedbackActionLogPage,
    setFeedbackActionLogPage,
    feedbackActionLogPageCount,
    feedbackActionLogSearch,
    setFeedbackActionLogSearch,
    pagedFeedbackActionLogs,
    selectedFeedbackActionLogs,
  } = feedback

  return (
    <TabsContent value="feedback" className="space-y-4">
      <div className="space-y-4">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <RotateCcw className="h-4 w-4" />
              反馈纠错历史
            </CardTitle>
            <CardDescription>
              查看 feedback correction 的判定、修改轨迹与回退结果
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_180px_180px]">
              <Input
                value={feedbackSearch}
                onChange={(event) => setFeedbackSearch(event.target.value)}
                placeholder="搜索查询编号 / 会话 / 查询内容 / 原因"
              />
              <Select value={feedbackStatusFilter} onValueChange={setFeedbackStatusFilter}>
                <SelectTrigger>
                  <SelectValue placeholder="按任务状态筛选" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">全部任务状态</SelectItem>
                  <SelectItem value="applied">已应用</SelectItem>
                  <SelectItem value="skipped">已跳过</SelectItem>
                  <SelectItem value="error">失败</SelectItem>
                  <SelectItem value="running">处理中</SelectItem>
                  <SelectItem value="pending">待处理</SelectItem>
                </SelectContent>
              </Select>
              <Select value={feedbackRollbackFilter} onValueChange={setFeedbackRollbackFilter}>
                <SelectTrigger>
                  <SelectValue placeholder="按回退状态筛选" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">全部回退状态</SelectItem>
                  <SelectItem value="none">未回退</SelectItem>
                  <SelectItem value="rolled_back">已回退</SelectItem>
                  <SelectItem value="error">回退失败</SelectItem>
                  <SelectItem value="running">回退中</SelectItem>
                </SelectContent>
              </Select>
            </div>

            <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl border bg-background/70 px-3 py-2 text-sm text-muted-foreground">
              <span>当前命中 {filteredFeedbackCorrections.length} 条记录，已加载最近 {feedbackCorrections.length} 条</span>
              <span>第 {feedbackPage} / {feedbackPageCount} 页，每页显示 {FEEDBACK_CORRECTION_PAGE_SIZE} 条</span>
            </div>

            <div className="grid items-start gap-4 xl:grid-cols-[minmax(0,0.92fr)_minmax(0,1.08fr)]">
              <ScrollArea className="h-[720px] rounded-lg border">
                <div className="space-y-3 p-3">
                  {pagedFeedbackCorrections.length > 0 ? pagedFeedbackCorrections.map((item) => {
                    const isSelected = selectedFeedbackCorrection?.task_id === item.task_id
                    const preview = getFeedbackCorrectionPreview(item)
                    const impactSummary = buildFeedbackImpactSummary(item)
                    return (
                      <button
                        key={item.task_id}
                        type="button"
                        onClick={() => setSelectedFeedbackTaskId(item.task_id)}
                        className={cn(
                          'w-full rounded-xl border p-4 text-left transition-colors',
                          isSelected
                            ? 'border-primary bg-primary/5 shadow-sm'
                            : 'bg-muted/20 hover:border-primary/40 hover:bg-muted/40',
                        )}
                      >
                        <div className="flex flex-col gap-3">
                          <div className="flex flex-wrap items-start justify-between gap-2">
                            <div className="flex flex-wrap items-center gap-2">
                              <Badge variant={getFeedbackStatusVariant(item.task_status)}>
                                {formatFeedbackTaskStatus(item.task_status)}
                              </Badge>
                              <Badge variant={getFeedbackStatusVariant(item.rollback_status)}>
                                {formatFeedbackRollbackStatus(item.rollback_status)}
                              </Badge>
                              <Badge variant="outline">
                                {formatFeedbackDecision(item.decision)}
                              </Badge>
                            </div>
                            <div className="text-[11px] text-muted-foreground">
                              {formatDeleteOperationTime(item.query_timestamp ?? item.created_at)}
                            </div>
                          </div>
                          <div className="space-y-1">
                            <div className="text-sm font-semibold break-words">
                              {preview.headline}
                            </div>
                            <div className="text-xs text-muted-foreground break-words">
                              查询：{item.query_text || '无查询文本'}
                            </div>
                          </div>
                          {(preview.oldRelation || preview.newRelation) ? (
                            <div className="grid gap-2 rounded-lg border bg-background/70 p-3 text-xs shadow-sm">
                              <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] sm:items-stretch">
                                <div className="rounded-md border border-amber-500/20 bg-amber-500/5 p-2">
                                  <div className="text-[11px] font-medium text-amber-700 dark:text-amber-300">纠错前</div>
                                  <div className="mt-1 break-words">{preview.oldRelation || '无'}</div>
                                </div>
                                <div className="hidden items-center text-muted-foreground sm:flex">→</div>
                                <div className="rounded-md border border-emerald-500/20 bg-emerald-500/5 p-2">
                                  <div className="text-[11px] font-medium text-emerald-700 dark:text-emerald-300">纠错后</div>
                                  <div className="mt-1 break-words">{preview.newRelation || '无'}</div>
                                </div>
                              </div>
                            </div>
                          ) : null}
                          <div className="flex flex-wrap gap-2">
                            {impactSummary.length > 0 ? impactSummary.slice(0, 3).map((summary) => (
                              <Badge key={`${item.task_id}:${summary}`} variant="secondary" className="font-normal">
                                {summary}
                              </Badge>
                            )) : (
                              <Badge variant="secondary" className="font-normal">
                                暂无影响摘要
                              </Badge>
                            )}
                          </div>
                          <div className="font-mono text-[11px] break-all text-muted-foreground">
                            {item.query_tool_id}
                          </div>
                        </div>
                      </button>
                    )
                  }) : (
                    <div className="rounded-lg border border-dashed bg-muted/20 p-6 text-center text-sm text-muted-foreground">
                      当前筛选条件下没有纠错历史
                    </div>
                  )}
                </div>
              </ScrollArea>

              <div className="self-start rounded-xl border bg-muted/20 p-4">
                {selectedFeedbackCorrection ? (
                  <div className="space-y-4">
                    <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
                      <div className="space-y-2">
                        <div className="flex flex-wrap items-center gap-2">
                          <Badge variant={getFeedbackStatusVariant(String(selectedFeedbackResolved?.task_status ?? ''))}>
                            {formatFeedbackTaskStatus(String(selectedFeedbackResolved?.task_status ?? ''))}
                          </Badge>
                          <Badge variant={getFeedbackStatusVariant(String(selectedFeedbackResolved?.rollback_status ?? 'none'))}>
                            {formatFeedbackRollbackStatus(String(selectedFeedbackResolved?.rollback_status ?? 'none'))}
                          </Badge>
                          <Badge variant="outline">
                            {formatFeedbackDecision(String(selectedFeedbackResolved?.decision ?? ''))}
                          </Badge>
                        </div>
                        <div className="text-base font-semibold break-words">
                          {selectedFeedbackPreview.headline}
                        </div>
                        <div className="text-sm text-muted-foreground break-words">
                          查询：{selectedFeedbackResolved?.query_text || '无查询文本'}
                        </div>
                        <div className="font-mono text-xs break-all text-muted-foreground">
                          {selectedFeedbackResolved?.query_tool_id}
                        </div>
                      </div>
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={openFeedbackRollbackDialog}
                        disabled={
                          String(selectedFeedbackResolved?.task_status ?? '') !== 'applied'
                          || String(selectedFeedbackResolved?.rollback_status ?? 'none') === 'rolled_back'
                          || feedbackRollingBack
                        }
                      >
                        <RotateCcw className="mr-2 h-4 w-4" />
                        {String(selectedFeedbackResolved?.rollback_status ?? 'none') === 'rolled_back'
                          ? '已回退'
                          : '回退本次纠错'}
                      </Button>
                    </div>

                    <div className="grid gap-4 xl:grid-cols-[minmax(0,1.05fr)_minmax(0,0.95fr)]">
                      <div className="rounded-xl border bg-background/70 p-4 shadow-sm">
                        <div className="text-sm font-semibold">本次纠错结论</div>
                        <div className="mt-3 grid gap-3 md:grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] md:items-stretch">
                          <div className="rounded-lg border border-amber-500/20 bg-amber-500/5 p-3">
                            <div className="text-xs font-medium text-amber-700 dark:text-amber-300">纠错前</div>
                            <div className="mt-2 text-sm break-words">
                              {selectedFeedbackPreview.oldRelation || '当前详情没有记录旧结论'}
                            </div>
                          </div>
                          <div className="hidden items-center justify-center text-muted-foreground md:flex">→</div>
                          <div className="rounded-lg border border-emerald-500/20 bg-emerald-500/5 p-3">
                            <div className="text-xs font-medium text-emerald-700 dark:text-emerald-300">纠错后</div>
                            <div className="mt-2 text-sm break-words">
                              {selectedFeedbackPreview.newRelation || '当前详情没有记录新结论'}
                            </div>
                          </div>
                        </div>
                      </div>

                      <div className="rounded-xl border bg-background/70 p-4 shadow-sm">
                        <div className="text-sm font-semibold">影响范围摘要</div>
                        <div className="mt-3 flex flex-wrap gap-2">
                          {selectedFeedbackImpactSummary.length > 0 ? selectedFeedbackImpactSummary.map((summary) => (
                            <Badge key={summary} variant="secondary" className="bg-primary/10 font-normal text-primary hover:bg-primary/15">
                              {summary}
                            </Badge>
                          )) : (
                            <div className="text-sm text-muted-foreground">当前没有可展示的影响范围摘要</div>
                          )}
                        </div>
                      </div>
                    </div>

                    <div className="grid gap-3 lg:grid-cols-4">
                      <div className="rounded-lg border bg-background/60 p-3">
                        <div className="text-xs text-muted-foreground">会话</div>
                        <div className="mt-1 text-sm break-all">{selectedFeedbackResolved?.session_id || '-'}</div>
                      </div>
                      <div className="rounded-lg border bg-background/60 p-3">
                        <div className="text-xs text-muted-foreground">反馈消息数</div>
                        <div className="mt-1 text-sm">{Number(selectedFeedbackResolved?.feedback_message_count ?? 0)}</div>
                      </div>
                      <div className="rounded-lg border bg-background/60 p-3">
                        <div className="text-xs text-muted-foreground">判定置信度</div>
                        <div className="mt-1 text-sm">{Number(selectedFeedbackResolved?.decision_confidence ?? 0).toFixed(2)}</div>
                      </div>
                      <div className="rounded-lg border bg-background/60 p-3">
                        <div className="text-xs text-muted-foreground">回退时间</div>
                        <div className="mt-1 text-sm">{formatDeleteOperationTime(selectedFeedbackResolved?.rolled_back_at)}</div>
                      </div>
                    </div>

                    {selectedFeedbackTaskLoading ? (
                      <div className="rounded-lg border bg-background/60 p-4">
                        <ThinkingIllustration size="sm" />
                      </div>
                    ) : null}

                    {selectedFeedbackTaskError ? (
                      <Alert variant="destructive">
                        <AlertDescription>{selectedFeedbackTaskError}</AlertDescription>
                      </Alert>
                    ) : null}

                    {selectedFeedbackResolved?.rollback_error ? (
                      <Alert variant="destructive">
                        <AlertDescription>{selectedFeedbackResolved.rollback_error}</AlertDescription>
                      </Alert>
                    ) : null}

                    <div className="grid gap-4 xl:grid-cols-[minmax(0,1.05fr)_minmax(0,0.95fr)]">
                      <div className="rounded-xl border bg-background/70 p-4">
                        <div className="text-sm font-semibold">回退后会发生什么</div>
                        <div className="mt-3 space-y-2 text-sm text-muted-foreground">
                          <div>会恢复旧关系状态，并撤销本次纠错写入的段落与关系。</div>
                          <div>会清理旧段落的待复核标记，并重新触发相关 Episode / Profile 修复。</div>
                          <div>如果你当前只是核对结果，可以先查看下面的详细数据，不必立刻执行回退。</div>
                        </div>
                      </div>
                      <div className="rounded-xl border bg-background/70 p-4">
                        <div className="text-sm font-semibold">处理摘要</div>
                        <div className="mt-3 grid gap-2 text-sm text-muted-foreground">
                          <div>判定：{formatFeedbackDecision(String(selectedFeedbackResolved?.decision ?? ''))}</div>
                          <div>任务状态：{formatFeedbackTaskStatus(String(selectedFeedbackResolved?.task_status ?? ''))}</div>
                          <div>回退状态：{formatFeedbackRollbackStatus(String(selectedFeedbackResolved?.rollback_status ?? 'none'))}</div>
                          <div>反馈消息数：{Number(selectedFeedbackResolved?.feedback_message_count ?? 0)}</div>
                        </div>
                      </div>
                    </div>

                    <div className="space-y-3">
                      <div className="text-sm font-semibold">详细数据</div>
                      <div className="grid gap-3 xl:grid-cols-2">
                        <details className="rounded-lg border bg-background/70 p-3">
                          <summary className="cursor-pointer text-sm font-medium">查询快照 JSON</summary>
                          <pre className="mt-3 max-h-56 overflow-auto text-xs break-words whitespace-pre-wrap">
                            {JSON.stringify(selectedFeedbackResolved?.query_snapshot ?? {}, null, 2)}
                          </pre>
                        </details>
                        <details className="rounded-lg border bg-background/70 p-3">
                          <summary className="cursor-pointer text-sm font-medium">判定结果 JSON</summary>
                          <pre className="mt-3 max-h-56 overflow-auto text-xs break-words whitespace-pre-wrap">
                            {JSON.stringify(selectedFeedbackResolved?.decision_payload ?? {}, null, 2)}
                          </pre>
                        </details>
                        <details className="rounded-lg border bg-background/70 p-3">
                          <summary className="cursor-pointer text-sm font-medium">回退计划摘要 JSON</summary>
                          <pre className="mt-3 max-h-64 overflow-auto text-xs break-words whitespace-pre-wrap">
                            {JSON.stringify(selectedFeedbackResolved?.rollback_plan_summary ?? {}, null, 2)}
                          </pre>
                        </details>
                        <details className="rounded-lg border bg-background/70 p-3">
                          <summary className="cursor-pointer text-sm font-medium">回退结果 JSON</summary>
                          <pre className="mt-3 max-h-64 overflow-auto text-xs break-words whitespace-pre-wrap">
                            {JSON.stringify(selectedFeedbackResolved?.rollback_result ?? {}, null, 2)}
                          </pre>
                        </details>
                      </div>
                    </div>

                    <details className="rounded-xl border bg-background/70 p-4">
                      <summary className="cursor-pointer text-sm font-semibold">
                        动作时间线
                      </summary>
                      <div className="mt-4 space-y-2">
                        <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
                          <div className="text-xs text-muted-foreground">
                            第 {feedbackActionLogPage} / {feedbackActionLogPageCount} 页，每页 {FEEDBACK_ACTION_LOG_PAGE_SIZE} 项
                          </div>
                          <Input
                            value={feedbackActionLogSearch}
                            onChange={(event) => setFeedbackActionLogSearch(event.target.value)}
                            placeholder="搜索动作 / 目标哈希 / 预览内容"
                            className="lg:w-80"
                          />
                        </div>
                        <ScrollArea className="h-[240px] rounded-lg border bg-background/60">
                          <div className="space-y-2 p-3">
                            {pagedFeedbackActionLogs.length > 0 ? pagedFeedbackActionLogs.map((item: MemoryFeedbackActionLogPayload) => (
                              <div key={`${item.id}:${item.action_type}`} className="rounded-lg border bg-muted/20 p-3">
                                <div className="flex flex-wrap items-center justify-between gap-2">
                                  <div className="flex flex-wrap items-center gap-2">
                                    <Badge variant="outline">{formatFeedbackActionType(item.action_type)}</Badge>
                                    {item.target_hash ? (
                                      <span className="font-mono text-[11px] break-all text-muted-foreground">{item.target_hash}</span>
                                    ) : null}
                                  </div>
                                  <div className="text-[11px] text-muted-foreground">
                                    {formatDeleteOperationTime(item.created_at)}
                                  </div>
                                </div>
                                <div className="mt-2 text-sm break-words">
                                  {describeFeedbackActionLog(item)}
                                </div>
                                {item.reason ? (
                                  <div className="mt-2 text-xs text-muted-foreground break-words">
                                    原因：{item.reason}
                                  </div>
                                ) : null}
                                {item.before_payload && Object.keys(item.before_payload).length > 0 ? (
                                  <div className="mt-3 rounded-md border bg-background/70 p-2 text-xs break-words">
                                    <span className="font-medium">处理前：</span>
                                    <span className="text-muted-foreground">{summarizeFeedbackActionPayload(item.before_payload)}</span>
                                  </div>
                                ) : null}
                                {item.after_payload && Object.keys(item.after_payload).length > 0 ? (
                                  <div className="mt-2 rounded-md border bg-background/70 p-2 text-xs break-words">
                                    <span className="font-medium">处理后：</span>
                                    <span className="text-muted-foreground">{summarizeFeedbackActionPayload(item.after_payload)}</span>
                                  </div>
                                ) : null}
                              </div>
                            )) : (
                              <div className="rounded-lg border border-dashed bg-muted/20 p-6 text-center text-sm text-muted-foreground">
                                {selectedFeedbackActionLogs.length > 0 ? '当前筛选条件下没有动作日志' : '当前任务没有动作日志'}
                              </div>
                            )}
                          </div>
                        </ScrollArea>
                        <div className="flex items-center justify-between gap-2">
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() => setFeedbackActionLogPage((current) => Math.max(1, current - 1))}
                            disabled={feedbackActionLogPage <= 1}
                          >
                            上一页
                          </Button>
                          <div className="text-xs text-muted-foreground">支持按动作类型、目标哈希和摘要检索</div>
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() => setFeedbackActionLogPage((current) => Math.min(feedbackActionLogPageCount, current + 1))}
                            disabled={feedbackActionLogPage >= feedbackActionLogPageCount}
                          >
                            下一页
                          </Button>
                        </div>
                      </div>
                    </details>
                  </div>
                ) : (
                  <div className="flex min-h-[360px] items-center justify-center rounded-lg border border-dashed bg-background/40 p-6 text-center text-sm text-muted-foreground">
                    当前没有可查看的纠错详情
                  </div>
                )}
              </div>
            </div>

            <div className="flex items-center justify-between gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => setFeedbackPage((current) => Math.max(1, current - 1))}
                disabled={feedbackPage <= 1}
              >
                上一页
              </Button>
              <div className="text-xs text-muted-foreground">
                支持按查询内容、任务状态和回退状态检索
              </div>
              <Button
                variant="outline"
                size="sm"
                onClick={() => setFeedbackPage((current) => Math.min(feedbackPageCount, current + 1))}
                disabled={feedbackPage >= feedbackPageCount}
              >
                下一页
              </Button>
            </div>
          </CardContent>
        </Card>
      </div>
    </TabsContent>
  )
}
