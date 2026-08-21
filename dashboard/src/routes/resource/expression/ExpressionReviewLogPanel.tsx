import { RefreshCw, RotateCcw, XCircle } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { ThinkingIllustration } from '@/components/ui/thinking-illustration'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { useToast } from '@/hooks/use-toast'
import { formatChatDisplayName } from '@/lib/chat-display'
import {
  approveExpressionReviewLog,
  getExpressionChatTargets,
  getExpressionReviewLogs,
} from '@/lib/expression-api'

import type { ChatInfo, ExpressionReviewLogEntry } from '@/types/expression'

const ALL_CHATS_VALUE = '__all__'

interface ExpressionReviewLogPanelProps {
  onRescued?: () => void
}

function formatTime(timestamp: number | null): string {
  if (!timestamp) return '-'
  return new Date(timestamp * 1000).toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function ReviewStatusBadge() {
  return (
    <Badge variant="destructive" className="gap-1 whitespace-nowrap">
      <XCircle className="h-3 w-3" />
      未通过
    </Badge>
  )
}

function RescueBadge({ entry }: { entry: ExpressionReviewLogEntry }) {
  if (!entry.rescued) return null
  return (
    <Badge variant="secondary" className="whitespace-nowrap">
      已救回 #{entry.rescued_expression_id ?? '-'}
    </Badge>
  )
}

export function ExpressionReviewLogPanel({ onRescued }: ExpressionReviewLogPanelProps) {
  const [entries, setEntries] = useState<ExpressionReviewLogEntry[]>([])
  const [chatList, setChatList] = useState<ChatInfo[]>([])
  const [chatFilter, setChatFilter] = useState(ALL_CHATS_VALUE)
  const [loading, setLoading] = useState(false)
  const [processingId, setProcessingId] = useState<string | null>(null)
  const { toast } = useToast()

  const loadLogs = useCallback(async () => {
    try {
      setLoading(true)
      const result = await getExpressionReviewLogs({
        limit: 100,
        passed: false,
        chat_id: chatFilter === ALL_CHATS_VALUE ? undefined : chatFilter,
      })
      setEntries(result.data)
    } catch (error) {
      toast({
        title: '加载失败',
        description: error instanceof Error ? error.message : '无法加载 AI 审核记录',
        variant: 'destructive',
      })
    } finally {
      setLoading(false)
    }
  }, [chatFilter, toast])

  const loadChatList = useCallback(async () => {
    try {
      // 用 allSettled：任一数据源失败仍以另一数据源构建聊天流映射（保留原 best-effort 行为）
      const [targetResult, logResult] = await Promise.allSettled([
        getExpressionChatTargets(),
        getExpressionReviewLogs({ limit: 200 }),
      ])
      const chatMap = new Map<string, ChatInfo>()
      if (targetResult.status === 'fulfilled') {
        targetResult.value.forEach((chat) => {
          chatMap.set(chat.chat_id, chat)
        })
      }
      if (logResult.status === 'fulfilled') {
        logResult.value.data.forEach((entry) => {
          if (!entry.session_id || chatMap.has(entry.session_id)) return
          chatMap.set(entry.session_id, {
            chat_id: entry.session_id,
            chat_name: entry.chat_name || entry.session_id,
            platform: null,
            is_group: false,
            use_expression: false,
            enable_learning: false,
          })
        })
      }
      setChatList([...chatMap.values()])
    } catch (error) {
      console.error('加载聊天流列表失败:', error)
    }
  }, [])

  const handleApprove = useCallback(
    async (entry: ExpressionReviewLogEntry) => {
      try {
        setProcessingId(entry.id)
        const result = await approveExpressionReviewLog(entry.id)
        toast({
          title: '已人工通过',
          description: result.message,
        })
        await loadLogs()
        onRescued?.()
      } catch (error) {
        toast({
          title: '恢复失败',
          description: error instanceof Error ? error.message : '未知错误',
          variant: 'destructive',
        })
      } finally {
        setProcessingId(null)
      }
    },
    [loadLogs, onRescued, toast]
  )

  useEffect(() => {
    loadLogs()
  }, [loadLogs])

  useEffect(() => {
    loadChatList()
  }, [loadChatList])

  return (
    <div className="bg-card flex h-full min-h-0 flex-col overflow-hidden rounded-lg border">
      <div className="flex shrink-0 flex-col gap-3 border-b p-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h2 className="text-lg font-semibold">AI 审核记录</h2>
          <p className="text-muted-foreground text-sm">
            最近 {entries.length} 条未通过的表达方式学习审核记录
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Select value={chatFilter} onValueChange={setChatFilter}>
            <SelectTrigger className="w-56">
              <SelectValue placeholder="选择聊天流" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL_CHATS_VALUE}>全部聊天流</SelectItem>
              {chatList.map((chat) => (
                <SelectItem key={chat.chat_id} value={chat.chat_id}>
                  {formatChatDisplayName(chat.chat_name, chat.account_id)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button variant="outline" size="icon" onClick={loadLogs} disabled={loading}>
            <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
          </Button>
        </div>
      </div>

      <div className="hidden min-h-0 flex-1 overflow-auto md:block">
        <Table aria-label="表达方式 AI 审核记录">
          <TableHeader className="bg-card sticky top-0 z-10">
            <TableRow>
              <TableHead className="w-32">时间</TableHead>
              <TableHead className="w-24">结果</TableHead>
              <TableHead>表达方式</TableHead>
              <TableHead>理由</TableHead>
              <TableHead className="w-48">聊天流</TableHead>
              <TableHead className="w-32 text-right">操作</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {loading ? (
              <TableRow>
                <TableCell colSpan={6} className="text-muted-foreground py-8 text-center">
                  <ThinkingIllustration size="sm" className="mx-auto" />
                </TableCell>
              </TableRow>
            ) : entries.length === 0 ? (
              <TableRow>
                <TableCell colSpan={6} className="text-muted-foreground py-8 text-center">
                  暂无 AI 审核记录
                </TableCell>
              </TableRow>
            ) : (
              entries.map((entry) => (
                <TableRow key={entry.id}>
                  <TableCell className="text-muted-foreground text-xs whitespace-nowrap">
                    {formatTime(entry.created_at)}
                  </TableCell>
                  <TableCell>
                    <div className="flex flex-wrap gap-1.5">
                      <ReviewStatusBadge />
                      <RescueBadge entry={entry} />
                    </div>
                  </TableCell>
                  <TableCell className="max-w-sm">
                    <div className="space-y-1">
                      <div className="truncate font-medium" title={entry.situation}>
                        {entry.situation}
                      </div>
                      <div className="text-muted-foreground truncate text-sm" title={entry.style}>
                        {entry.style}
                      </div>
                    </div>
                  </TableCell>
                  <TableCell className="max-w-xs">
                    <div className="line-clamp-2 text-sm" title={entry.reason}>
                      {entry.reason || entry.error || '-'}
                    </div>
                  </TableCell>
                  <TableCell
                    className="max-w-[12rem] truncate"
                    title={entry.chat_name || entry.session_id}
                  >
                    {entry.chat_name || entry.session_id}
                  </TableCell>
                  <TableCell className="text-right">
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => handleApprove(entry)}
                      disabled={entry.rescued || processingId === entry.id}
                    >
                      <RotateCcw className="mr-1 h-4 w-4" />
                      人工通过
                    </Button>
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </div>

      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-4 md:hidden">
        {loading ? (
          <div className="text-muted-foreground py-8 text-center">
            <ThinkingIllustration size="sm" className="mx-auto" />
          </div>
        ) : entries.length === 0 ? (
          <div className="text-muted-foreground py-8 text-center">暂无 AI 审核记录</div>
        ) : (
          entries.map((entry) => (
            <div key={entry.id} className="bg-card space-y-3 rounded-lg border p-4">
              <div className="flex items-center justify-between gap-2">
                <div className="text-muted-foreground text-xs">{formatTime(entry.created_at)}</div>
                <div className="flex flex-wrap justify-end gap-1.5">
                  <ReviewStatusBadge />
                  <RescueBadge entry={entry} />
                </div>
              </div>
              <div>
                <div className="text-muted-foreground mb-1 text-xs">情景</div>
                <div className="text-sm font-medium break-all">{entry.situation}</div>
              </div>
              <div>
                <div className="text-muted-foreground mb-1 text-xs">风格</div>
                <div className="text-sm break-all">{entry.style}</div>
              </div>
              <div>
                <div className="text-muted-foreground mb-1 text-xs">理由</div>
                <div className="text-sm break-all">{entry.reason || entry.error || '-'}</div>
              </div>
              <div className="text-muted-foreground border-t pt-3 text-xs">
                <span className="truncate" title={entry.chat_name || entry.session_id}>
                  {entry.chat_name || entry.session_id}
                </span>
              </div>
              <Button
                variant="outline"
                size="sm"
                className="w-full"
                onClick={() => handleApprove(entry)}
                disabled={entry.rescued || processingId === entry.id}
              >
                <RotateCcw className="mr-1 h-4 w-4" />
                人工通过救回
              </Button>
            </div>
          ))
        )}
      </div>
    </div>
  )
}
