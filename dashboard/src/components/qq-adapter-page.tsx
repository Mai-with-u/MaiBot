import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeftRight, RefreshCw } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'

import { QQMappingTable } from '@/components/qq-mapping-table'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { ScrollArea } from '@/components/ui/scroll-area'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { CopyButton } from '@/components/ui/stateful-button'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { useHostRuntime } from '@/host/state'
import { getPersonList } from '@/lib/person-api'
import { invokeQQWebUI } from '@/lib/qq-webui'

const query = (name: string) => invokeQQWebUI('queries', name)

interface LoginState {
  connected: boolean
  enabled: boolean
  configured_app_id: string
  bot_name: string
  state: string
  message: string
  image: string
}

function LoginPanel() {
  const runtime = useHostRuntime()
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const login = useQuery({
    queryKey: ['qq-login', runtime.selected_bot_id],
    queryFn: async () => (await query('login_status')) as LoginState,
    refetchInterval: open ? 2000 : 10000,
  })
  const state = login.data
  async function act(name: string) {
    setBusy(true)
    setError('')
    try {
      await invokeQQWebUI('actions', name, {}, name === 'login_start')
      await login.refetch()
    } catch (error) {
      setError(String(error))
    } finally {
      setBusy(false)
    }
  }
  const pending = state && ['creating', 'waiting', 'saving'].includes(state.state)
  return (
    <Card>
      <CardContent className="space-y-4 py-4 sm:py-5">
        {login.error && (
          <Alert variant="destructive">
            <AlertDescription>{String(login.error)}</AlertDescription>
          </Alert>
        )}
        <div className="flex flex-wrap items-center gap-3">
          <Badge variant={state?.connected ? 'default' : 'secondary'}>
            {login.isPending ? '读取中…' : state?.connected ? '已连接' : '未连接'}
          </Badge>
          <span className="font-medium">{state?.bot_name}</span>
          {state?.configured_app_id && (
            <span className="text-muted-foreground text-sm">AppID {state.configured_app_id}</span>
          )}
          <Dialog open={open} onOpenChange={setOpen}>
            <DialogTrigger asChild>
              <Button variant="outline" className="ml-auto">
                {state?.configured_app_id ? '登录设置' : '扫码登录'}
              </Button>
            </DialogTrigger>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>{state?.configured_app_id ? '重新登录' : '扫码登录'}</DialogTitle>
              </DialogHeader>
              <p className="text-muted-foreground text-sm">
                {state?.configured_app_id
                  ? '授权后将替换当前机器人账号。'
                  : '使用手机 QQ 扫码授权。'}
              </p>
              {error && (
                <Alert variant="destructive">
                  <AlertDescription>{error}</AlertDescription>
                </Alert>
              )}
              {state?.image && (
                <img src={state.image} alt="QQ 登录二维码" className="mx-auto h-56 w-56" />
              )}
              {state?.message && state.state !== 'idle' && (
                <p role="status" className="text-sm">
                  {state.message}
                </p>
              )}
              <div className="flex gap-2">
                <Button disabled={busy || !!pending} onClick={() => void act('login_start')}>
                  生成二维码
                </Button>
                {pending && (
                  <Button
                    variant="outline"
                    disabled={busy || state?.state === 'saving'}
                    onClick={() => void act('login_cancel')}
                  >
                    取消扫码
                  </Button>
                )}
              </div>
            </DialogContent>
          </Dialog>
        </div>
      </CardContent>
    </Card>
  )
}

function UserDirectory({ mapping }: { mapping: boolean }) {
  const runtime = useHostRuntime()
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(30)
  const [copyError, setCopyError] = useState('')
  const viewport = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (viewport.current) viewport.current.scrollTop = 0
  }, [page, search, pageSize])
  const users = useQuery({
    queryKey: ['qq-user-directory', runtime.selected_bot_id, search, page, pageSize, mapping],
    queryFn: () =>
      getPersonList({
        platform: 'qq',
        search,
        page,
        page_size: pageSize,
        exclude_numeric_user_ids: !mapping,
      }),
    refetchInterval: 30000,
  })
  async function copy(id: string): Promise<boolean> {
    try {
      await navigator.clipboard.writeText(id)
      setCopyError('')
      return true
    } catch (error) {
      setCopyError(`复制失败：${String(error)}`)
      return false
    }
  }
  return (
    <div className="flex h-full min-h-0 flex-col gap-3">
      <div className="flex shrink-0 items-center gap-3">
        <Input
          aria-label="搜索用户"
          placeholder="搜索昵称、名称或 ID"
          value={search}
          onChange={(event) => {
            setSearch(event.target.value)
            setPage(1)
          }}
          className="max-w-md"
        />
        <span className="text-muted-foreground text-sm">{users.data?.total ?? 0} 位用户</span>
        <Button
          variant="outline"
          size="icon"
          aria-label="刷新用户"
          onClick={() => void users.refetch()}
          disabled={users.isFetching}
        >
          <RefreshCw className="size-4" />
        </Button>
      </div>
      {(users.error || copyError) && (
        <Alert variant="destructive">
          <AlertDescription>{copyError || String(users.error)}</AlertDescription>
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
              <TableHead>用户</TableHead>
              <TableHead>用户 ID</TableHead>
              <TableHead className="w-20">复制</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {users.data?.data.map((user) => (
              <TableRow key={user.person_id}>
                <TableCell>
                  <div>{user.nickname || user.person_name || '未命名'}</div>
                  {user.person_name && user.person_name !== user.nickname && (
                    <div className="text-muted-foreground text-xs">{user.person_name}</div>
                  )}
                </TableCell>
                <TableCell className="font-mono break-all whitespace-normal select-all">
                  {user.user_id}
                </TableCell>
                <TableCell>
                  <CopyButton
                    iconOnly
                    variant="ghost"
                    size="icon-xs"
                    aria-label={`复制 ${user.user_id}`}
                    iconClassName="size-4"
                    onCopy={() => copy(user.user_id)}
                  />
                </TableCell>
              </TableRow>
            ))}
            {!users.data?.data.length && (
              <TableRow>
                <TableCell colSpan={3} className="text-muted-foreground py-8 text-center">
                  {users.isPending
                    ? '加载中…'
                    : users.error
                      ? '加载失败，请重试'
                      : '没有匹配的用户'}
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
        <Button
          variant="outline"
          disabled={page === 1 || users.isFetching}
          onClick={() => setPage(page - 1)}
        >
          上一页
        </Button>
        <span className="text-sm">
          {page} / {Math.max(1, Math.ceil((users.data?.total ?? 0) / pageSize))}
        </span>
        <Button
          variant="outline"
          disabled={users.isFetching || page * pageSize >= (users.data?.total ?? 0)}
          onClick={() => setPage(page + 1)}
        >
          下一页
        </Button>
      </div>
    </div>
  )
}

export function QQAdapterPage({ initialTab = 'users' }: { initialTab?: string }) {
  const runtime = useHostRuntime()
  const [tab, setTab] = useState(initialTab)
  const client = useQueryClient()
  const [savingMode, setSavingMode] = useState(false)
  const [modeError, setModeError] = useState('')
  const mode = useQuery({
    queryKey: ['qq-mapping-mode', runtime.selected_bot_id],
    queryFn: async () => (await query('identity_mode')) as { mapping_enabled: boolean },
    enabled: runtime.selected_bot_running,
    refetchInterval: 10000,
  })
  const mapping = mode.data?.mapping_enabled === true
  async function toggleMode() {
    if (savingMode) return
    setSavingMode(true)
    setModeError('')
    try {
      const result = (await invokeQQWebUI('actions', 'set_mapping_mode', {
        enabled: !mapping,
      })) as { mapping_enabled: boolean }
      client.setQueryData(['qq-mapping-mode', runtime.selected_bot_id], result)
      await client.invalidateQueries({ queryKey: ['qq-user-directory', runtime.selected_bot_id] })
    } catch (error) {
      setModeError(String(error))
    } finally {
      setSavingMode(false)
    }
  }
  const active = !mapping && ['group', 'user'].includes(tab) ? 'users' : tab
  if (!runtime.selected_bot_running) return <p className="text-muted-foreground p-4">实例已停止</p>
  return (
    <div className="flex h-full min-h-0 flex-col gap-4 p-4">
      {(mode.error || modeError) && (
        <Alert variant="destructive">
          <AlertDescription>{modeError || String(mode.error)}</AlertDescription>
        </Alert>
      )}
      <Tabs value={active} onValueChange={setTab} className="flex min-h-0 flex-1 flex-col">
        <div className="flex shrink-0 flex-wrap items-center justify-between gap-3">
          <TabsList>
            <TabsTrigger value="users">用户列表</TabsTrigger>
            {mapping && (
              <>
                <TabsTrigger value="group">群映射</TabsTrigger>
                <TabsTrigger value="user">用户映射</TabsTrigger>
              </>
            )}
            <TabsTrigger value="login">登录状态</TabsTrigger>
          </TabsList>
          {mode.data && (
            <div className="flex items-center gap-2">
              <Badge variant="secondary">{mapping ? 'QQ 号映射' : '原始 OpenID'}</Badge>
              <Button
                variant="outline"
                size="xs"
                disabled={savingMode}
                onClick={() => void toggleMode()}
                title="仅修改身份模式，不迁移历史数据"
              >
                <ArrowLeftRight className="size-4" aria-hidden="true" />
                {savingMode ? '保存中…' : mapping ? '切换为官方模式' : '切换为映射模式'}
              </Button>
            </div>
          )}
        </div>
        <TabsContent value="users" className="min-h-0 flex-1 overflow-hidden">
          {mode.data ? (
            <UserDirectory key={String(mapping)} mapping={mapping} />
          ) : (
            <p className="text-muted-foreground p-4">读取身份模式…</p>
          )}
        </TabsContent>
        {mapping &&
          ['group', 'user'].map((view) => (
            <TabsContent key={view} value={view} className="min-h-0 flex-1 overflow-hidden">
              <QQMappingTable kind={view as 'group' | 'user'} />
            </TabsContent>
          ))}
        <TabsContent value="login" className="min-h-0 flex-1 overflow-auto">
          <LoginPanel />
        </TabsContent>
      </Tabs>
    </div>
  )
}
