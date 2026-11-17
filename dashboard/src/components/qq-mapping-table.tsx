import { useEffect, useRef, useState } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { RefreshCw, Trash2, UserRound, UsersRound } from 'lucide-react'

import { Alert, AlertDescription } from '@/components/ui/alert'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/avatar'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { ScrollArea } from '@/components/ui/scroll-area'
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
import { useHostRuntime } from '@/host/state'
import { invokeQQWebUI } from '@/lib/qq-webui'

interface Binding {
  openid: string
  id: string
  source: string
  nickname: string
  cardname: string
  group_id: string
  group_openid: string
  status: string
  conflict: boolean
}
interface Candidate {
  source: string
  targets: string[]
  status: string
  target: string
}
interface Mappings {
  group: Binding[]
  user: Binding[]
}

// 头像逐个进入共享请求队列，避免一页头像挡住刷新和删除操作。
let avatarQueue: Promise<unknown> = Promise.resolve()

function BindingAvatar({ kind, row }: { kind: 'group' | 'user'; row: Binding }) {
  const runtime = useHostRuntime()
  const anchor = useRef<HTMLSpanElement>(null)
  const [visible, setVisible] = useState(false)
  useEffect(() => {
    const element = anchor.current
    if (!element) return
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) {
        setVisible(true)
        observer.disconnect()
      }
    })
    observer.observe(element)
    return () => observer.disconnect()
  }, [])
  const avatar = useQuery({
    queryKey: [
      'qq-binding-avatar',
      runtime.selected_bot_id,
      kind,
      row.openid,
      row.id,
      row.source,
      row.group_openid,
    ],
    queryFn: ({ signal }) => {
      const request = avatarQueue.then(async () => {
        signal.throwIfAborted()
        return (await invokeQQWebUI('queries', 'avatar', {
          kind,
          openid: row.openid,
          source: row.source,
          group_openid: row.group_openid,
        })) as { image: string; message?: string }
      })
      avatarQueue = request.catch(() => undefined)
      return request
    },
    enabled: visible,
    staleTime: 24 * 60 * 60 * 1000,
    retry: false,
  })
  const label = `${kind === 'group' ? '群' : '用户'} ${row.id}`
  return (
    <Avatar
      ref={anchor}
      className="size-7"
      title={avatar.error ? String(avatar.error) : avatar.data?.message || label}
    >
      <AvatarImage src={avatar.data?.image || undefined} alt={`${label}的头像`} />
      <AvatarFallback>
        {kind === 'group' ? <UsersRound className="size-4" /> : <UserRound className="size-4" />}
      </AvatarFallback>
    </Avatar>
  )
}

export function QQMappingTable({ kind }: { kind: 'group' | 'user' }) {
  const navigate = useNavigate()
  const runtime = useHostRuntime()
  const client = useQueryClient()
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(30)
  const [selected, setSelected] = useState<string[]>([])
  const [confirmation, setConfirmation] = useState<string[] | 'finish' | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const viewport = useRef<HTMLDivElement>(null)
  const bindings = useQuery({
    queryKey: ['qq-bindings', runtime.selected_bot_id, kind],
    queryFn: async () => {
      const data = (await invokeQQWebUI('queries', 'mappings')) as Mappings
      const migration =
        true && kind === 'user'
          ? ((await invokeQQWebUI('queries', 'mapping_migrations')) as { candidates: Candidate[] })
          : { candidates: [] }
      return { rows: data[kind], candidates: migration.candidates }
    },
    refetchInterval: busy || confirmation ? false : 30000,
  })
  const byOpenid = new Map<string, Binding>()
  for (const row of bindings.data?.rows ?? []) {
    if (row.source === 'union' && row.id === '—') continue
    const existing = byOpenid.get(row.openid)
    if (!existing || row.source === 'manual') byOpenid.set(row.openid, row)
  }
  const rows = Array.from(byOpenid.values()).filter((row) =>
    [row.id, row.openid, row.nickname, row.cardname, row.group_id].some((value) =>
      value.includes(search.trim())
    )
  )
  const pages = Math.max(1, Math.ceil(rows.length / pageSize))
  const current = Math.min(page, pages)
  const visible = rows.slice((current - 1) * pageSize, current * pageSize)
  const selectedRows = selected.filter((id) => byOpenid.has(id))
  const allOnPage = visible.length > 0 && visible.every((row) => selectedRows.includes(row.openid))
  const candidates = bindings.data?.candidates ?? []
  const canFinish =
    !!bindings.data && candidates.every((item) => ['migrated', 'empty'].includes(item.status))
  useEffect(() => {
    if (viewport.current) viewport.current.scrollTop = 0
  }, [current, pageSize, search])
  async function execute() {
    if (!confirmation || busy) return
    setBusy(true)
    setError('')
    try {
      if (confirmation === 'finish') {
        await invokeQQWebUI('actions', 'finish_mapping', {}, true)
        await client.invalidateQueries({ queryKey: ['qq-mapping-mode', runtime.selected_bot_id] })
      } else {
        for (let offset = 0; offset < confirmation.length; offset += 200) {
          const batch = confirmation.slice(offset, offset + 200)
          await invokeQQWebUI(
            'actions',
            'delete_bindings',
            { kind, openids_json: JSON.stringify(batch) },
            true
          )
          setSelected((current) => current.filter((id) => !batch.includes(id)))
          setConfirmation(confirmation.slice(offset + 200))
        }
        setSelected([])
        await client.invalidateQueries({ queryKey: ['qq-bindings', runtime.selected_bot_id] })
      }
      setConfirmation(null)
    } catch (error) {
      setError(String(error))
    } finally {
      setBusy(false)
    }
  }
  function migrate(row: Binding) {
    void navigate({
      to: '/data-transfer',
      search: { tab: 'user-migration', source: row.id, target: row.openid, fromMapping: true },
    })
  }

  return (
    <div className="flex h-full min-h-0 flex-col gap-3">
      <div className="flex shrink-0 flex-wrap items-center gap-2">
        <Input
          className="max-w-sm"
          placeholder={kind === 'group' ? '搜索群号或 OpenID' : '搜索昵称、QQ 号或 OpenID'}
          value={search}
          onChange={(event) => {
            setSearch(event.target.value)
            setPage(1)
          }}
        />
        <span className="text-muted-foreground text-sm">{rows.length} 条绑定</span>
        <Button
          variant="outline"
          size="icon-sm"
          aria-label="刷新绑定"
          disabled={bindings.isFetching || busy}
          onClick={() => void bindings.refetch()}
        >
          <RefreshCw className="size-4" />
        </Button>
        <Button
          variant="outline"
          size="sm"
          disabled={!selectedRows.length || busy}
          onClick={() => setConfirmation(selectedRows)}
        >
          <Trash2 className="size-4" />
          删除所选{selectedRows.length > 0 ? ` (${selectedRows.length})` : ''}
        </Button>
        {rows.length > visible.length && (
          <Button
            variant="ghost"
            size="sm"
            disabled={busy}
            onClick={() => setSelected(rows.map((row) => row.openid))}
          >
            全选 {rows.length} 条
          </Button>
        )}
        {selectedRows.length > 0 && (
          <Button variant="ghost" size="sm" onClick={() => setSelected([])}>
            取消选择
          </Button>
        )}
        {true && kind === 'user' && (
          <Button
            className="ml-auto"
            variant="outline"
            size="sm"
            disabled={!canFinish || busy}
            onClick={() => setConfirmation('finish')}
          >
            完成迁移并关闭映射
          </Button>
        )}
      </div>
      {(bindings.error || error) && (
        <Alert variant="destructive">
          <AlertDescription>{error || String(bindings.error)}</AlertDescription>
        </Alert>
      )}
      <ScrollArea
        className="min-h-0 flex-1 rounded-lg border"
        data-dashboard-table-wrapper="true"
        viewportRef={viewport}
        scrollbars="vertical"
      >
        <Table density="compact" framed={false}>
          <TableHeader>
            <TableRow>
              <TableHead className="w-10">
                <Checkbox
                  aria-label="选择本页全部绑定"
                  checked={
                    allOnPage
                      ? true
                      : visible.some((row) => selectedRows.includes(row.openid))
                        ? 'indeterminate'
                        : false
                  }
                  onCheckedChange={(checked) =>
                    setSelected(
                      checked
                        ? [...new Set([...selectedRows, ...visible.map((row) => row.openid)])]
                        : selectedRows.filter((id) => !visible.some((row) => row.openid === id))
                    )
                  }
                  disabled={busy}
                />
              </TableHead>
              <TableHead>{kind === 'group' ? '群号' : 'QQ 号 / 用户'}</TableHead>
              <TableHead>OpenID</TableHead>
              <TableHead>来源 / 状态</TableHead>
              <TableHead>操作</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {visible.map((row) => {
              const migration = candidates.find((item) => item.source === row.id)
              return (
                <TableRow key={row.openid}>
                  <TableCell>
                    <Checkbox
                      aria-label={`选择 ${row.openid}`}
                      checked={selectedRows.includes(row.openid)}
                      disabled={busy}
                      onCheckedChange={(checked) =>
                        setSelected(
                          checked
                            ? [...selectedRows, row.openid]
                            : selectedRows.filter((id) => id !== row.openid)
                        )
                      }
                    />
                  </TableCell>
                  <TableCell>
                    <div className="flex items-center gap-2">
                      <BindingAvatar kind={kind} row={row} />
                      <div>
                        <div className="select-all">{row.id}</div>
                        {(row.nickname || row.cardname) && (
                          <div className="text-muted-foreground text-xs">
                            {row.nickname || row.cardname}
                          </div>
                        )}
                      </div>
                    </div>
                  </TableCell>
                  <TableCell className="font-mono break-all whitespace-normal select-all">
                    {row.openid}
                  </TableCell>
                  <TableCell>
                    <div>
                      {row.source === 'manual'
                        ? '手动关联'
                        : row.source === 'auto'
                          ? '静默匹配'
                          : '官方关联'}
                    </div>
                    <div
                      className={
                        row.conflict ? 'text-destructive text-xs' : 'text-muted-foreground text-xs'
                      }
                    >
                      {row.status}
                    </div>
                  </TableCell>
                  <TableCell>
                    <div className="flex items-center gap-2">
                      {true && kind === 'user' && migration?.status === 'pending' && (
                        <Button
                          variant="outline"
                          size="xs"
                          disabled={busy}
                          onClick={() => migrate(row)}
                        >
                          迁回此 OpenID
                        </Button>
                      )}
                      {true && kind === 'user' && migration?.status === 'migrated' && (
                        <span className="text-muted-foreground text-xs">已迁移</span>
                      )}
                      {true && kind === 'user' && migration?.status === 'conflict' && (
                        <span className="text-destructive text-xs">身份冲突</span>
                      )}
                      <Button
                        variant="ghost"
                        size="icon-xs"
                        aria-label={`删除 ${row.openid}`}
                        disabled={busy}
                        onClick={() => setConfirmation([row.openid])}
                      >
                        <Trash2 className="size-4" />
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              )
            })}
            {!visible.length && (
              <TableRow>
                <TableCell colSpan={5} className="text-muted-foreground py-8 text-center">
                  {bindings.isPending ? '加载中…' : '暂无绑定'}
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </ScrollArea>
      <div className="flex shrink-0 flex-wrap items-center justify-end gap-3">
        <Select
          value={String(pageSize)}
          onValueChange={(value) => {
            setPageSize(Number(value))
            setPage(1)
          }}
        >
          <SelectTrigger className="mr-auto w-32" aria-label="每页条数">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {[15, 30, 50, 100].map((size) => (
              <SelectItem key={size} value={String(size)}>
                {size} 条 / 页
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Button variant="outline" disabled={current === 1} onClick={() => setPage(current - 1)}>
          上一页
        </Button>
        <span className="text-sm">
          {current} / {pages}
        </span>
        <Button variant="outline" disabled={current === pages} onClick={() => setPage(current + 1)}>
          下一页
        </Button>
      </div>
      <AlertDialog
        open={confirmation !== null}
        onOpenChange={(open) => {
          if (!open && !busy) setConfirmation(null)
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {confirmation === 'finish'
                ? '关闭映射？'
                : `删除 ${confirmation?.length ?? 0} 条绑定？`}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {confirmation === 'finish'
                ? '后续使用原始 OpenID。'
                : '删除所选 OpenID 的关联及静默匹配记录，适配器会短暂重连。历史用户数据保留，新消息可触发重新匹配。'}
            </AlertDialogDescription>
          </AlertDialogHeader>
          {error && <p className="text-destructive text-sm">{error}</p>}
          <AlertDialogFooter>
            <AlertDialogCancel disabled={busy}>取消</AlertDialogCancel>
            <AlertDialogAction
              disabled={busy}
              onClick={(event) => {
                event.preventDefault()
                void execute()
              }}
            >
              {busy ? '处理中…' : '确认'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
