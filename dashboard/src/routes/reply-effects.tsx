import { useCallback, useEffect, useMemo, useState } from 'react'
import { BarChart3, RefreshCw } from 'lucide-react'
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Dialog, DialogBody, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { backendApi } from '@/lib/http'

interface Aggregate {
  name: string
  count: number
  response_score: number | null
  reception_score: number | null
  conversation_score: number | null
  raw_score: number | null
  relative_score: number | null
  confidence: number | null
}

interface Overview {
  summary: Aggregate
  strategies: Aggregate[]
  versions: Aggregate[]
  trend: Aggregate[]
  filters: {
    sessions: [string, string][]
    strategies: string[]
    models: string[]
  }
}

interface RecordItem {
  effect_id: string
  session_name: string
  status: string
  created_at: string
  strategy_primary: string
  model_name: string
  reply_text: string
  response_score: number | null
  reception_score: number | null
  conversation_score: number | null
  raw_score: number | null
  relative_score: number | null
  confidence: number
  evaluation_error: string
}

interface RecordList {
  items: RecordItem[]
  total: number
  next_cursor: number | null
}

const STRATEGY_NAMES: Record<string, string> = {
  answer: '信息回答',
  opinion: '观点表达',
  empathy: '共情支持',
  humor: '玩梗调侃',
  question: '追问引导',
  topic_start: '主动开题',
  acknowledgement: '简短接话',
  other: '其他',
}

function scoreText(value: number | null) {
  return value === null ? '—' : value.toFixed(1)
}

export function ReplyEffectsPage() {
  const [overview, setOverview] = useState<Overview | null>(null)
  const [records, setRecords] = useState<RecordList | null>(null)
  const [detail, setDetail] = useState<Record<string, unknown> | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [sessionId, setSessionId] = useState('')
  const [strategy, setStrategy] = useState('')
  const [startAt, setStartAt] = useState('')
  const [endAt, setEndAt] = useState('')
  const [minConfidence, setMinConfidence] = useState('0.6')

  const query = useMemo(() => {
    const params = new URLSearchParams()
    if (sessionId) params.set('session_id', sessionId)
    if (strategy) params.set('strategy', strategy)
    if (startAt) params.set('start_at', `${startAt}T00:00:00`)
    if (endAt) params.set('end_at', `${endAt}T23:59:59`)
    params.set('min_confidence', minConfidence || '0')
    return params.toString()
  }, [endAt, minConfidence, sessionId, startAt, strategy])

  const refresh = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const [nextOverview, nextRecords] = await Promise.all([
        backendApi.get<Overview>(`/api/webui/reply-effects/overview?${query}`),
        backendApi.get<RecordList>(`/api/webui/reply-effects?${query}&limit=50`),
      ])
      setOverview(nextOverview)
      setRecords(nextRecords)
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : '加载回复效果数据失败')
    } finally {
      setLoading(false)
    }
  }, [query])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const openDetail = async (effectId: string) => {
    try {
      setDetail(await backendApi.get<Record<string, unknown>>(`/api/webui/reply-effects/${effectId}`))
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : '加载详情失败')
    }
  }

  const summary = overview?.summary
  return (
    <div className="mx-auto flex w-full max-w-[1600px] flex-col gap-6 p-4 md:p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="flex items-center gap-2 text-2xl font-semibold"><BarChart3 className="h-6 w-6" />回复效果</h1>
          <p className="text-muted-foreground mt-1 text-sm">分析 MaiSaka 回复的回应度、情感接受度与聊天推动度。</p>
        </div>
        <Button variant="outline" onClick={() => void refresh()} disabled={loading}>
          <RefreshCw className={`mr-2 h-4 w-4 ${loading ? 'animate-spin' : ''}`} />刷新
        </Button>
      </div>

      <Card>
        <CardContent className="grid gap-3 pt-6 md:grid-cols-5">
          <select className="border-input bg-background h-10 rounded-md border px-3 text-sm" value={sessionId} onChange={(event) => setSessionId(event.target.value)}>
            <option value="">全部聊天流</option>
            {overview?.filters.sessions.map(([id, name]) => <option key={id} value={id}>{name}</option>)}
          </select>
          <select className="border-input bg-background h-10 rounded-md border px-3 text-sm" value={strategy} onChange={(event) => setStrategy(event.target.value)}>
            <option value="">全部策略</option>
            {Object.entries(STRATEGY_NAMES).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
          <Input type="date" aria-label="开始日期" value={startAt} onChange={(event) => setStartAt(event.target.value)} />
          <Input type="date" aria-label="结束日期" value={endAt} onChange={(event) => setEndAt(event.target.value)} />
          <Input type="number" min="0" max="1" step="0.1" aria-label="最低置信度" value={minConfidence} onChange={(event) => setMinConfidence(event.target.value)} />
        </CardContent>
      </Card>

      {error && <div className="border-destructive/30 bg-destructive/10 text-destructive rounded-lg border p-4 text-sm">{error}</div>}
      {!loading && summary?.count === 0 && (
        <Card><CardContent className="text-muted-foreground py-14 text-center">暂无符合条件的已完成记录。请先在配置中启用“记录回复效果”，或降低最低置信度。</CardContent></Card>
      )}

      <div className="grid gap-4 md:grid-cols-5">
        {[
          ['样本数', summary?.count ?? 0],
          ['回应度', scoreText(summary?.response_score ?? null)],
          ['情感接受度', scoreText(summary?.reception_score ?? null)],
          ['聊天推动度', scoreText(summary?.conversation_score ?? null)],
          ['相对分', scoreText(summary?.relative_score ?? null)],
        ].map(([label, value]) => (
          <Card key={label}><CardHeader className="pb-2"><CardDescription>{label}</CardDescription><CardTitle className="text-3xl">{value}</CardTitle></CardHeader></Card>
        ))}
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader><CardTitle>趋势</CardTitle><CardDescription>按天汇总三个维度</CardDescription></CardHeader>
          <CardContent className="h-72">
            <ResponsiveContainer width="100%" height="100%"><BarChart data={overview?.trend ?? []}><CartesianGrid strokeDasharray="3 3" /><XAxis dataKey="name" /><YAxis domain={[0, 100]} /><Tooltip /><Legend /><Bar dataKey="response_score" name="回应度" fill="var(--chart-1)" /><Bar dataKey="reception_score" name="接受度" fill="var(--chart-2)" /><Bar dataKey="conversation_score" name="推动度" fill="var(--chart-3)" /></BarChart></ResponsiveContainer>
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>策略对比</CardTitle><CardDescription>固定语义策略及当前评分器版本</CardDescription></CardHeader>
          <CardContent><Table><TableHeader><TableRow><TableHead>策略</TableHead><TableHead>样本</TableHead><TableHead>原始分</TableHead><TableHead>相对分</TableHead><TableHead>置信度</TableHead></TableRow></TableHeader><TableBody>
            {overview?.strategies.map((item) => <TableRow key={item.name}><TableCell>{STRATEGY_NAMES[item.name] ?? item.name}</TableCell><TableCell>{item.count}</TableCell><TableCell>{scoreText(item.raw_score)}</TableCell><TableCell>{scoreText(item.relative_score)}</TableCell><TableCell>{scoreText(item.confidence)}</TableCell></TableRow>)}
          </TableBody></Table></CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader><CardTitle>模型与 Prompt 版本</CardTitle><CardDescription>按回复模型和实际请求 Prompt 指纹聚合，便于比较版本变化</CardDescription></CardHeader>
        <CardContent><Table><TableHeader><TableRow><TableHead>版本</TableHead><TableHead>样本</TableHead><TableHead>回应度</TableHead><TableHead>接受度</TableHead><TableHead>推动度</TableHead><TableHead>相对分</TableHead></TableRow></TableHeader><TableBody>
          {overview?.versions.map((item) => <TableRow key={item.name}><TableCell className="font-mono text-xs">{item.name}</TableCell><TableCell>{item.count}</TableCell><TableCell>{scoreText(item.response_score)}</TableCell><TableCell>{scoreText(item.reception_score)}</TableCell><TableCell>{scoreText(item.conversation_score)}</TableCell><TableCell>{scoreText(item.relative_score)}</TableCell></TableRow>)}
        </TableBody></Table></CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>单条回复</CardTitle><CardDescription>共 {records?.total ?? 0} 条，点击查看上下文和归因证据</CardDescription></CardHeader>
        <CardContent><Table><TableHeader><TableRow><TableHead>时间 / 聊天流</TableHead><TableHead>回复</TableHead><TableHead>策略</TableHead><TableHead>回应</TableHead><TableHead>接受</TableHead><TableHead>推动</TableHead><TableHead>相对分</TableHead></TableRow></TableHeader><TableBody>
          {records?.items.map((item) => <TableRow key={item.effect_id} className="cursor-pointer" onClick={() => void openDetail(item.effect_id)}><TableCell><div>{new Date(item.created_at).toLocaleString()}</div><div className="text-muted-foreground text-xs">{item.session_name}</div></TableCell><TableCell className="max-w-md truncate">{item.reply_text || item.evaluation_error || '无文本'}</TableCell><TableCell>{STRATEGY_NAMES[item.strategy_primary] ?? item.strategy_primary}</TableCell><TableCell>{scoreText(item.response_score)}</TableCell><TableCell>{scoreText(item.reception_score)}</TableCell><TableCell>{scoreText(item.conversation_score)}</TableCell><TableCell>{scoreText(item.relative_score)}</TableCell></TableRow>)}
        </TableBody></Table></CardContent>
      </Card>

      <Dialog open={detail !== null} onOpenChange={(open) => { if (!open) setDetail(null) }}>
        <DialogContent className="[--dialog-width:72rem]"><DialogHeader><DialogTitle>回复效果证据详情</DialogTitle></DialogHeader><DialogBody className="max-h-[75vh]"><pre className="bg-muted overflow-auto rounded-lg p-4 text-xs whitespace-pre-wrap">{JSON.stringify(detail, null, 2)}</pre></DialogBody></DialogContent>
      </Dialog>
    </div>
  )
}
