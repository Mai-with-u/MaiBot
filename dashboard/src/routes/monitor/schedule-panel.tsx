import { RefreshCw } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { backendApi } from '@/lib/http'

import { InspectorRow, InspectorSection } from './inspector-rows'

interface ScheduleItem {
  id: string
  text: string
  session_id: string
  session_name: string
  kind: 'bot' | 'user'
  created_at: number
  due_at: number | null
  condition: string
  status: 'pending' | 'dispatching' | 'triggered' | 'failed' | 'completed' | 'cancelled'
  error?: string
}

const STATUS_LABELS: Record<ScheduleItem['status'], string> = {
  pending: '待处理',
  dispatching: '执行中',
  triggered: '已触发',
  failed: '执行失败',
  completed: '已完成',
  cancelled: '已取消',
}

function formatDate(timestamp: number): string {
  return new Date(timestamp * 1000).toLocaleString()
}

/** 所有聊天流的事项快照，仅在展开时读取并定时刷新。 */
export function ScheduleInspectorView() {
  const [items, setItems] = useState<ScheduleItem[] | null>(null)
  const [includeCompleted, setIncludeCompleted] = useState(false)
  const [query, setQuery] = useState('')
  const [refreshKey, setRefreshKey] = useState(0)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    let inFlight = false
    const load = async () => {
      if (inFlight) return
      inFlight = true
      setLoading(true)
      try {
        const result = await backendApi.get<{ items: ScheduleItem[] }>('/api/webui/schedules', {
          query: { include_completed: includeCompleted },
          signal: controller.signal,
        })
        if (!controller.signal.aborted) {
          setItems(result.items)
          setError(null)
        }
      } catch (cause) {
        if (!controller.signal.aborted) {
          setError(cause instanceof Error ? cause.message : '读取事项失败')
        }
      } finally {
        inFlight = false
        if (!controller.signal.aborted) setLoading(false)
      }
    }
    setItems(null)
    setError(null)
    void load()
    const timer = window.setInterval(() => void load(), 10000)
    return () => {
      controller.abort()
      window.clearInterval(timer)
    }
  }, [includeCompleted, refreshKey])

  const visibleItems = useMemo(() => {
    const normalized = query.trim().toLowerCase()
    return (items ?? [])
      .filter((item) => !normalized || `${item.text} ${item.session_name}`.toLowerCase().includes(normalized))
      .sort((a, b) => {
        const archivedA = a.status === 'completed' || a.status === 'cancelled'
        const archivedB = b.status === 'completed' || b.status === 'cancelled'
        return Number(archivedA) - Number(archivedB)
          || (a.due_at ?? Infinity) - (b.due_at ?? Infinity)
          || b.created_at - a.created_at
      })
  }, [items, query])

  return (
    <>
      <div className="space-y-2 p-3">
        <Input
          controlSize="sm"
          placeholder="搜索事项或聊天名称"
          aria-label="搜索事项或聊天名称"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        <div className="flex items-center justify-between gap-2">
          <label className="flex cursor-pointer items-center gap-2 text-xs">
            <Checkbox checked={includeCompleted} onCheckedChange={(checked) => setIncludeCompleted(checked === true)} />
            显示已完成和已取消
          </label>
          <Button variant="outline" size="icon-xs" aria-label="刷新事项" title="刷新事项" disabled={loading} onClick={() => setRefreshKey((key) => key + 1)}>
            <RefreshCw className={loading ? 'animate-spin' : undefined} />
          </Button>
        </div>
        <p className="text-muted-foreground text-xs">每 10 秒自动刷新</p>
      </div>
      <InspectorSection title="所有事项" meta={items === null ? '—' : `${visibleItems.length} 项`} />
      {error && <InspectorRow className="text-destructive break-words">读取事项失败：{error}</InspectorRow>}
      {!error && items === null && <InspectorRow className="text-muted-foreground">正在读取…</InspectorRow>}
      {!error && items !== null && visibleItems.length === 0 && (
        <InspectorRow className="text-muted-foreground">{query.trim() ? '没有匹配的事项' : '暂无事项'}</InspectorRow>
      )}
      {visibleItems.map((item) => (
        <InspectorRow key={item.id} className="flex-col !items-stretch gap-1.5">
          <div className="flex min-w-0 items-center gap-2">
            <span className="text-muted-foreground min-w-0 flex-1 truncate" title={item.session_name}>{item.session_name}</span>
            <Badge variant={item.status === 'failed' ? 'destructive' : 'outline'}>{STATUS_LABELS[item.status]}</Badge>
          </div>
          <p className="whitespace-pre-wrap break-words">{item.text}</p>
          <p className="text-muted-foreground">{item.kind === 'bot' ? '麦麦日程' : '用户待办'} · {item.due_at === null ? '未设置时间' : formatDate(item.due_at)}</p>
          {item.condition && <p className="text-muted-foreground whitespace-pre-wrap break-words">触发条件：{item.condition}</p>}
          {item.error && <p className="text-destructive whitespace-pre-wrap break-words">{item.error}</p>}
        </InspectorRow>
      ))}
    </>
  )
}
