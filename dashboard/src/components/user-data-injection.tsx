import { useEffect, useState } from 'react'
import { useNavigate } from '@tanstack/react-router'

import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { useHostRuntime } from '@/host/state'
import { backendApi } from '@/lib/http'

interface Preview {
  kind?: 'union' | 'injection'
  preview_id?: string
  status?: string
  ready?: boolean
  error?: string
  record?: string
  counts?: Record<string, Record<string, number>>
  conflicts?: {
    id: string
    field: string
    source: unknown
    target: unknown
    source_id: string
    target_id: string
  }[]
  decisions?: Record<string, string>
  relations?: { source: string; target: string }[]
}
const endpoint = '/api/webui/person-migrations/user-injection'

function validatePreview(preview: Preview): Preview {
  if (
    preview.status === 'preview' && preview.kind === 'injection' &&
    (typeof preview.ready !== 'boolean' || !preview.preview_id ||
      !Array.isArray(preview.conflicts) || !preview.counts || !preview.decisions ||
      !preview.relations?.length)
  ) {
    return { kind: 'injection', status: 'invalid', error: '预览数据不完整，请重新预览' }
  }
  return preview
}

/** 宿主保存在线预览；确认执行时在线校验并合并数据。 */
export function UserDataInjection({
  initialSource = '',
  initialTarget = '',
  fromMapping = false,
}: {
  initialSource?: string
  initialTarget?: string
  fromMapping?: boolean
}) {
  const navigate = useNavigate()
  const [source, setSource] = useState(initialSource)
  const [target, setTarget] = useState(initialTarget)
  const runtime = useHostRuntime()
  const [preview, setPreview] = useState<Preview>({})
  const [decisions, setDecisions] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)
  const [elapsed, setElapsed] = useState(0)
  const [actionLabel, setActionLabel] = useState('正在处理')
  useEffect(() => {
    if (!busy) return
    const started = Date.now()
    const timer = window.setInterval(() => setElapsed(Math.floor((Date.now() - started) / 1000)), 1000)
    return () => window.clearInterval(timer)
  }, [busy])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [confirmed, setConfirmed] = useState(false)

  async function refresh() {
    setLoading(true)
    try {
      const result = await backendApi.get<{ preview: Preview }>(endpoint)
      setPreview(validatePreview(result.preview))
      setDecisions(result.preview.decisions ?? {})
      return result
    } finally {
      setLoading(false)
    }
  }
  useEffect(() => {
    let active = true
    setLoading(true)
    setError('')
    setPreview({})
    setConfirmed(false)
    backendApi
      .get<{ preview: Preview }>(endpoint)
      .then((result) => {
        if (!active) return
        setPreview(validatePreview(result.preview))
        setDecisions(result.preview.decisions ?? {})
      })
      .catch((error) => {
        if (active) setError(String(error))
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [runtime.selected_bot_id])

  async function act(action: 'preview' | 'execute' | 'cancel') {
    if (busy) return
    setElapsed(0)
    setActionLabel(action === 'preview' ? '正在生成预览' : action === 'execute' ? '正在合并数据' : '正在取消预览')
    setBusy(true)
    setError('')
    setPreview((current) => ({ ...current, error: undefined }))
    setConfirmed(false)
    try {
      const result = await backendApi.post<Preview>(`${endpoint}/${action}`, {
        body:
          action === 'preview'
            ? {
                source:
                  preview.status === 'preview' ? preview.relations?.[0]?.source : source.trim(),
                target:
                  preview.status === 'preview' ? preview.relations?.[0]?.target : target.trim(),
                decisions,
              }
            : { preview_id: preview.preview_id },
      })
      setPreview(validatePreview(result))
      setDecisions(result.decisions ?? {})
      await refresh()
    } catch (error) {
      setError(String(error))
      try {
        await refresh()
      } catch {
        /* 保留原始操作错误，用户可显式刷新宿主状态。 */
      }
    } finally {
      setBusy(false)
    }
  }
  const otherPending = preview.status === 'preview' && preview.kind !== 'injection'
  const pending = preview.status === 'preview'
  if (otherPending)
    return (
      <Alert className="m-4">
        <AlertDescription>
          存在旧 Union 迁移预览，该功能已停用。请取消旧预览。
          <Button
            disabled={busy}
            onClick={() => {
              void act('cancel')
            }}
          >
            取消旧预览
          </Button>
          {error && <p>{error}</p>}
        </AlertDescription>
      </Alert>
    )
  const conflicts = preview.conflicts ?? []
  const canExecute =
    pending &&
    preview.ready &&
    conflicts.every((item) => decisions[item.id] === preview.decisions?.[item.id])
  return (
    <Card className="m-4">
      <CardContent className="space-y-4 py-4 sm:py-5">
        {fromMapping && (
          <div className="flex items-center justify-between gap-3">
            <span className="text-sm">QQ 号 → OpenID</span>
            <Button
              variant="outline"
              disabled={busy || pending}
              onClick={() =>
                void navigate({
                  to: '/extensions/$pluginId/$pageId',
                  params: { pluginId: 'Mai-with-u.qq-adapter', pageId: 'group-mappings' },
                  search: { state: { view: 'user' } },
                })
              }
            >
              返回迁移进度
            </Button>
          </div>
        )}
        {(error || preview.error) && (
          <Alert variant="destructive">
            <AlertDescription>{error || preview.error}</AlertDescription>
          </Alert>
        )}
        {!pending && (
          <div className="flex flex-wrap items-end gap-3">
            <label htmlFor="user-migration-source" className="space-y-2 text-sm">
              来源用户 ID
              <Input
                id="user-migration-source"
                value={source}
                disabled={busy}
                onChange={(event) => {
                  setSource(event.target.value)
                  setDecisions({})
                }}
                placeholder="QQ 号或 OpenID"
              />
            </label>
            <span aria-hidden="true">→</span>
            <label htmlFor="user-migration-target" className="space-y-2 text-sm">
              目标用户 ID
              <Input
                id="user-migration-target"
                value={target}
                disabled={busy}
                onChange={(event) => {
                  setTarget(event.target.value)
                  setDecisions({})
                }}
                placeholder="QQ 号或 OpenID"
              />
            </label>
            <Button
              disabled={
                busy ||
                loading ||
                !source.trim() ||
                !target.trim() ||
                source.trim() === target.trim()
              }
              onClick={() => {
                void act('preview')
              }}
            >
              预览迁移
            </Button>
          </div>
        )}
        {pending && (
          <div className="space-y-3">
            <p>预览完成 · 尚未迁移</p>
            <div className="max-h-48 overflow-auto text-sm">
              {preview.relations?.map((item) => (
                <p key={item.source}>
                  {item.source} → {item.target}
                </p>
              ))}
            </div>
            <div className="flex flex-wrap gap-3">
              {Object.entries(preview.counts ?? {}).map(([name, counts]) => (
                <div key={name} className="rounded border px-4 py-3 text-sm">
                  <p className="text-muted-foreground">
                    {name === 'main' ? '人物资料' : name.startsWith('memory-') ? '长期记忆' : name}
                  </p>
                  <p className="text-lg font-semibold">
                    {Object.values(counts).reduce((sum, count) => sum + count, 0)} 条变更
                  </p>
                </div>
              ))}
              <div className="rounded border px-4 py-3 text-sm">
                <p className="text-muted-foreground">待选择冲突</p>
                <p className="text-lg font-semibold">
                  {conflicts.filter((item) => !decisions[item.id]).length} 项
                </p>
              </div>
            </div>
            {conflicts.map((item) => (
              <div key={item.id} className="space-y-2 rounded border p-3">
                <p className="text-sm break-all">
                  {item.source_id} → {item.target_id}：{item.field}
                </p>
                <Select
                  value={decisions[item.id] ?? ''}
                  onValueChange={(value) => {
                    setDecisions((current) => ({ ...current, [item.id]: value }))
                    setConfirmed(false)
                  }}
                  disabled={busy}
                >
                  <SelectTrigger>
                    <SelectValue placeholder="逐项选择保留哪一方的值" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="source">来源：{JSON.stringify(item.source)}</SelectItem>
                    <SelectItem value="target">目标：{JSON.stringify(item.target)}</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            ))}
            {!canExecute && preview.relations && (
              <Button
                disabled={busy || conflicts.some((item) => !decisions[item.id])}
                onClick={() => {
                  void act('preview')
                }}
              >
                应用冲突选择
              </Button>
            )}
            {canExecute && (
              <label htmlFor="user-migration-confirm" className="flex gap-2 text-sm">
                <Checkbox
                  id="user-migration-confirm"
                  checked={confirmed}
                  disabled={busy}
                  onCheckedChange={(value) => setConfirmed(value === true)}
                />
                合并资料与记忆，后续消息归入目标用户
              </label>
            )}
            <div className="flex gap-2">
              <Button
                disabled={busy || !canExecute || !confirmed}
                onClick={() => {
                  void act('execute')
                }}
              >
                确认合并
              </Button>
              <Button
                variant="outline"
                disabled={busy}
                onClick={() => {
                  void act('cancel')
                }}
              >
                取消预览
              </Button>
            </div>
          </div>
        )}
        {preview.status === 'completed' && (
          <p className="text-sm break-all">迁移完成，执行记录：{preview.record}</p>
        )}
        {busy && (
          <p role="status" className="text-sm">
            {actionLabel} · {elapsed} 秒
          </p>
        )}
      </CardContent>
    </Card>
  )
}
