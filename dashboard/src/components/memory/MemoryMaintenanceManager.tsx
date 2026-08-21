import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Lock, RefreshCw, RotateCcw, Shield, Snowflake } from 'lucide-react'

import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { ScrollArea } from '@/components/ui/scroll-area'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { ThinkingIllustration } from '@/components/ui/thinking-illustration'
import { useToast } from '@/hooks/use-toast'
import {
  freezeMemory,
  getMemoryRecycleBin,
  protectMemory,
  reinforceMemory,
  restoreMaintainedMemory,
  type MemoryMaintenanceActionPayload,
  type MemoryMaintenanceItemPayload,
} from '@/lib/memory-api'
import { cn } from '@/lib/utils'

export type MemoryMaintenanceAction = 'reinforce' | 'freeze' | 'protect' | 'restore'

function formatMemoryTime(timestamp?: number | null): string {
  if (!timestamp) {
    return '-'
  }
  const normalized = timestamp > 1_000_000_000_000 ? timestamp : timestamp * 1000
  const value = new Date(normalized)
  if (Number.isNaN(value.getTime())) {
    return '-'
  }
  return value.toLocaleString('zh-CN', {
    hour12: false,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function parsePositiveInt(value: string, fallback: number): number {
  const parsed = Number(value)
  if (!Number.isInteger(parsed) || parsed <= 0) {
    return fallback
  }
  return parsed
}

function parseOptionalHours(value: string): number | undefined {
  const trimmed = value.trim()
  if (!trimmed) {
    return undefined
  }
  const parsed = Number(trimmed)
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : undefined
}

function getRelationTarget(item: MemoryMaintenanceItemPayload): string {
  return String(item.hash ?? item.relation_hash ?? '')
}

function getRelationText(item: MemoryMaintenanceItemPayload): string {
  const direct = String(item.text ?? '').trim()
  if (direct) {
    return direct
  }
  return [item.subject, item.predicate, item.object].map((value) => String(value ?? '').trim()).filter(Boolean).join(' ')
}

function getActionLabel(action: MemoryMaintenanceAction): string {
  switch (action) {
    case 'reinforce':
      return '强化'
    case 'freeze':
      return '冻结'
    case 'protect':
      return '保护'
    case 'restore':
      return '恢复'
    default:
      return action
  }
}

export interface MemoryMaintenanceManagerProps {
  initialTarget?: string
  initialAction?: MemoryMaintenanceAction
  onChanged?: () => Promise<void> | void
}

export function MemoryMaintenanceManager({
  initialTarget = '',
  initialAction = 'reinforce',
  onChanged,
}: MemoryMaintenanceManagerProps) {
  const { toast } = useToast()
  const [target, setTarget] = useState('')
  const [action, setAction] = useState<MemoryMaintenanceAction>(initialAction)
  const [protectHours, setProtectHours] = useState('')
  const [recycleLimit, setRecycleLimit] = useState('50')
  const [items, setItems] = useState<MemoryMaintenanceItemPayload[]>([])
  const [loading, setLoading] = useState(false)
  const [actionLoading, setActionLoading] = useState(false)
  const [itemSearch, setItemSearch] = useState('')
  const initialLoadedRef = useRef(false)
  const initialTargetAppliedRef = useRef('')

  const filteredItems = useMemo(() => {
    const keyword = itemSearch.trim().toLowerCase()
    if (!keyword) {
      return items
    }
    return items.filter((item) =>
      [
        getRelationTarget(item),
        getRelationText(item),
        item.source,
        item.subject,
        item.predicate,
        item.object,
      ].some((value) => String(value ?? '').toLowerCase().includes(keyword)),
    )
  }, [itemSearch, items])

  const loadRecycleBin = useCallback(async () => {
    setLoading(true)
    try {
      const payload = await getMemoryRecycleBin(parsePositiveInt(recycleLimit, 50))
      setItems(payload.items ?? [])
    } catch (error) {
      toast({
        title: '加载记忆回收站失败',
        description: error instanceof Error ? error.message : String(error),
        variant: 'destructive',
      })
    } finally {
      setLoading(false)
    }
  }, [recycleLimit, toast])

  useEffect(() => {
    if (initialLoadedRef.current) {
      return
    }
    initialLoadedRef.current = true
    void loadRecycleBin()
  }, [loadRecycleBin])

  useEffect(() => {
    const cleanTarget = initialTarget.trim()
    if (!cleanTarget || cleanTarget === initialTargetAppliedRef.current) {
      return
    }
    initialTargetAppliedRef.current = cleanTarget
    setTarget(cleanTarget)
    setItemSearch(cleanTarget)
  }, [initialTarget])

  useEffect(() => {
    setAction(initialAction)
  }, [initialAction])

  const runAction = useCallback(async (nextAction: MemoryMaintenanceAction, nextTarget: string) => {
    const cleanTarget = nextTarget.trim()
    if (!cleanTarget) {
      toast({
        title: '缺少维护目标',
        description: '请输入关系 hash 或查询文本。',
        variant: 'destructive',
      })
      return
    }
    if (nextAction === 'freeze' && !window.confirm('确认冻结命中的记忆关系？冻结后关系会从活跃图谱中移除。')) {
      return
    }
    if (nextAction === 'restore' && !window.confirm('确认恢复命中的记忆关系？')) {
      return
    }

    setActionLoading(true)
    try {
      let payload: MemoryMaintenanceActionPayload
      if (nextAction === 'reinforce') {
        payload = await reinforceMemory(cleanTarget)
      } else if (nextAction === 'freeze') {
        payload = await freezeMemory(cleanTarget)
      } else if (nextAction === 'protect') {
        payload = await protectMemory(cleanTarget, parseOptionalHours(protectHours))
      } else {
        payload = await restoreMaintainedMemory(cleanTarget)
      }
      toast({
        title: payload.success ? `记忆${getActionLabel(nextAction)}完成` : `记忆${getActionLabel(nextAction)}失败`,
        description: String(payload.detail ?? payload.error ?? ''),
        variant: payload.success ? 'default' : 'destructive',
      })
      await onChanged?.()
      await loadRecycleBin()
    } catch (error) {
      toast({
        title: `记忆${getActionLabel(nextAction)}失败`,
        description: error instanceof Error ? error.message : String(error),
        variant: 'destructive',
      })
    } finally {
      setActionLoading(false)
    }
  }, [loadRecycleBin, onChanged, protectHours, toast])

  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,0.8fr)_minmax(0,1.2fr)]">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Shield className="h-4 w-4" />
            记忆维护操作
          </CardTitle>
          <CardDescription>对关系 hash 或查询文本命中的长期记忆执行强化、冻结、保护和恢复。</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <Alert>
            <AlertDescription>
              维护目标沿用后端解析规则：优先匹配关系 hash，也可以输入查询文本让后端解析命中的关系。
            </AlertDescription>
          </Alert>
          <div className="space-y-2">
            <Label htmlFor="maintenance-target">维护目标</Label>
            <Input id="maintenance-target" value={target} onChange={(event) => setTarget(event.target.value)} placeholder="relation hash 或查询文本" />
          </div>
          <div className="grid gap-3 md:grid-cols-2">
            <div className="space-y-2">
              <Label>动作</Label>
              <Select value={action} onValueChange={(value) => setAction(value as MemoryMaintenanceAction)}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="reinforce">强化</SelectItem>
                  <SelectItem value="freeze">冻结</SelectItem>
                  <SelectItem value="protect">保护</SelectItem>
                  <SelectItem value="restore">恢复</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label htmlFor="maintenance-hours">保护时长（小时）</Label>
              <Input
                id="maintenance-hours"
                type="number"
                value={protectHours}
                onChange={(event) => setProtectHours(event.target.value)}
                placeholder="空值表示永久保护"
                disabled={action !== 'protect'}
              />
            </div>
          </div>
          <Button onClick={() => void runAction(action, target)} disabled={actionLoading}>
            {action === 'reinforce' ? <Lock className="mr-2 h-4 w-4" /> : null}
            {action === 'freeze' ? <Snowflake className="mr-2 h-4 w-4" /> : null}
            {action === 'protect' ? <Shield className="mr-2 h-4 w-4" /> : null}
            {action === 'restore' ? <RotateCcw className="mr-2 h-4 w-4" /> : null}
            执行{getActionLabel(action)}
          </Button>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <RotateCcw className="h-4 w-4" />
            记忆回收站
          </CardTitle>
          <CardDescription>查看已删除关系，并支持按行恢复。</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_140px_auto] md:items-end">
            <div className="space-y-2">
              <Label htmlFor="maintenance-search">筛选</Label>
              <Input id="maintenance-search" value={itemSearch} onChange={(event) => setItemSearch(event.target.value)} placeholder="按 hash、主体、谓词、来源筛选" />
            </div>
            <div className="space-y-2">
              <Label htmlFor="maintenance-limit">数量</Label>
              <Input id="maintenance-limit" type="number" value={recycleLimit} onChange={(event) => setRecycleLimit(event.target.value)} />
            </div>
            <Button variant="outline" onClick={() => void loadRecycleBin()} disabled={loading}>
              <RefreshCw className={cn('mr-2 h-4 w-4', loading && 'animate-spin')} />
              刷新
            </Button>
          </div>
          <div className="flex flex-wrap gap-2 text-sm text-muted-foreground">
            <Badge variant="outline">已加载 {items.length} 条</Badge>
            <Badge variant="secondary">当前命中 {filteredItems.length} 条</Badge>
          </div>
          <ScrollArea className="h-[520px]">
            <Table>
              <TableHeader className="sticky top-0 bg-background">
                <TableRow>
                  <TableHead>关系</TableHead>
                  <TableHead>删除时间</TableHead>
                  <TableHead>操作</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {filteredItems.length > 0 ? filteredItems.map((item, index) => {
                  const rowTarget = getRelationTarget(item)
                  return (
                    <TableRow key={`${rowTarget}:${index}`}>
                      <TableCell>
                        <div className="font-medium break-words">{getRelationText(item) || '-'}</div>
                        <div className="mt-1 font-mono text-[11px] text-muted-foreground break-all">{rowTarget || '-'}</div>
                        {item.source ? <Badge variant="outline" className="mt-2">{String(item.source)}</Badge> : null}
                      </TableCell>
                      <TableCell>{formatMemoryTime(item.deleted_at ?? item.updated_at)}</TableCell>
                      <TableCell>
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() => void runAction('restore', rowTarget)}
                          disabled={!rowTarget || actionLoading}
                        >
                          恢复
                        </Button>
                      </TableCell>
                    </TableRow>
                  )
                }) : (
                  <TableRow>
                    <TableCell colSpan={3} className="text-center text-muted-foreground">
                      {loading ? <ThinkingIllustration size="sm" className="mx-auto" /> : '回收站没有可展示的关系'}
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          </ScrollArea>
        </CardContent>
      </Card>
    </div>
  )
}
