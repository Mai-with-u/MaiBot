import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowRight, RefreshCw } from 'lucide-react'
import { useState, type ReactNode } from 'react'

import { MigrationValue, conflictTitle } from './MigrationValue'

import { DataPagination } from '@/components/ui/data-pagination'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { DataPanel } from '@/components/ui/surface'
import { getAllChatStreams } from '@/lib/chat-management-api'
import {
  executeMigration,
  getMigration,
  listMigrations,
  previewMigration,
  recoverMigration,
  type MigrationJob,
} from '@/lib/chat-migration-api'

const stages: Record<string, string> = {
  preview: '等待处理冲突',
  queued: '准备暂停聊天流',
  validating: '核对最新数据',
  memory: '覆盖目标长期记忆',
  configuration: '应用配置选择',
  database: '合并聊天数据',
  finalizing: '刷新聊天数据',
  completed: '迁移完成',
  failed: '迁移未完成，已恢复',
  recovery_required: '需要恢复',
}
const choices: Record<string, string> = { source: '采用来源', target: '保留目标', both: '两者保留' }
const terminal = new Set(['preview', 'completed', 'failed', 'recovery_required'])

export function ChatMigrationWorkspace({ headerActions }: { headerActions?: ReactNode }) {
  const [source, setSource] = useState('')
  const [target, setTarget] = useState('')
  const [search, setSearch] = useState('')
  const [conflictSearch, setConflictSearch] = useState('')
  const [conflictPage, setConflictPage] = useState(1)
  const [onlyPending, setOnlyPending] = useState(false)
  const [kindFilter, setKindFilter] = useState('all')
  const [historyPage, setHistoryPage] = useState(1)
  const [jobId, setJobId] = useState<string>()
  const [decisions, setDecisions] = useState<Record<string, string>>({})
  const [confirmed, setConfirmed] = useState(false)
  const [error, setError] = useState('')
  const client = useQueryClient()
  const streams = useQuery({ queryKey: ['chat-streams', 'all'], queryFn: getAllChatStreams })
  const history = useQuery({
    queryKey: ['chat-migrations'],
    queryFn: listMigrations,
    refetchInterval: 5000,
  })
  const jobQuery = useQuery({
    queryKey: ['chat-migration', jobId],
    queryFn: () => getMigration(jobId!),
    enabled: !!jobId,
    refetchInterval: (query) =>
      query.state.data && !terminal.has(query.state.data.status) ? 1500 : false,
  })
  const job = jobQuery.data
  const reset = () => {
    setJobId(undefined)
    setConflictPage(1)
    setDecisions({})
    setConfirmed(false)
    setError('')
  }
  const preview = useMutation({
    mutationFn: () => previewMigration(source, target),
    onSuccess: (result) => {
      client.setQueryData(['chat-migration', result.id], result)
      setJobId(result.id)
      setConflictPage(1)
      setDecisions({})
      setConfirmed(false)
      setError('')
      void client.invalidateQueries({ queryKey: ['chat-migrations'] })
    },
    onError: (err) => setError(err.message),
  })
  const execute = useMutation({
    mutationFn: () => executeMigration(job!.id, decisions, confirmed),
    onSuccess: () => {
      setError('')
      void jobQuery.refetch()
      void history.refetch()
    },
    onError: (err) => setError(err.message),
  })
  const recover = useMutation({
    mutationFn: (id: string) => recoverMigration(id),
    onSuccess: () => {
      setError('')
      void jobQuery.refetch()
      void history.refetch()
    },
    onError: (err) => setError(err.message),
  })
  const busy =
    preview.isPending ||
    execute.isPending ||
    recover.isPending ||
    (!!job && !terminal.has(job.status))
  const unresolved = job?.conflicts.filter((item) => !decisions[item.id]).length ?? 0
  const filteredConflicts =
    job?.conflicts.filter(
      (item) =>
        (!onlyPending || !decisions[item.id]) &&
        (kindFilter === 'all' || item.kind === kindFilter) &&
        conflictTitle(item).toLowerCase().includes(conflictSearch.toLowerCase())
    ) ?? []
  const currentPage = Math.min(conflictPage, Math.max(1, Math.ceil(filteredConflicts.length / 5)))
  const currentHistoryPage = Math.min(
    historyPage,
    Math.max(1, Math.ceil((history.data?.items.length ?? 0) / 5))
  )
  const name = (id: string) => {
    const chat = streams.data?.find((item) => item.session_id === id)
    if (chat) return `${chat.display_name} · ${chat.platform} · ${chat.target_id}`
    const identity = job?.identities[id]
    if (identity)
      return identity.group_name || `${identity.user_nickname || identity.user_id}的私聊`
    return '已删除的聊天流'
  }
  const open = (item: MigrationJob) => {
    setSource(item.source)
    setTarget(item.target)
    setJobId(item.id)
    setConflictPage(1)
    setDecisions({})
    setConfirmed(false)
    setError('')
  }
  return (
    <section className="space-y-6">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold">聊天数据迁移</h1>
          <p className="text-muted-foreground mt-2 text-sm">
            复制并合并聊天数据，来源保留。仅暂停迁移双方，完成后继续接收消息。
          </p>
        </div>
        {headerActions}
      </header>
      <DataPanel className="space-y-4 p-4">
        <Input
          aria-label="搜索聊天流"
          placeholder="按名称、平台或群号搜索聊天流"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <div className="grid items-end gap-3 md:grid-cols-[1fr_auto_1fr]">
          {(['source', 'target'] as const).map((side, index) => (
            <div key={side} className="contents">
              {index > 0 && (
                <ArrowRight
                  className="text-muted-foreground hidden self-center md:block"
                  aria-hidden
                />
              )}
              <Label className="space-y-2">
                {side === 'source' ? '来源聊天流（保留原数据）' : '目标聊天流（接收数据）'}
                <Select
                  value={side === 'source' ? source : target}
                  disabled={busy}
                  onValueChange={(value) => {
                    reset()
                    ;(side === 'source' ? setSource : setTarget)(value)
                  }}
                >
                  <SelectTrigger aria-label={side === 'source' ? '来源聊天流' : '目标聊天流'}>
                    <SelectValue placeholder="选择聊天流" />
                  </SelectTrigger>
                  <SelectContent>
                    {streams.data
                      ?.filter(
                        (chat) =>
                          chat.session_id === source ||
                          chat.session_id === target ||
                          `${chat.display_name} ${chat.platform} ${chat.target_id}`
                            .toLowerCase()
                            .includes(search.toLowerCase())
                      )
                      .map((chat) => (
                        <SelectItem key={chat.session_id} value={chat.session_id}>
                          {chat.display_name} · {chat.platform} · {chat.target_id}
                        </SelectItem>
                      ))}
                  </SelectContent>
                </Select>
              </Label>
            </div>
          ))}
        </div>
        {streams.isError && <p role="alert">无法加载聊天流：{streams.error.message}</p>}
        <Button
          disabled={busy || !source || !target || source === target}
          onClick={() => preview.mutate()}
        >
          {preview.isPending ? '正在统计数据…' : '预览迁移与冲突'}
        </Button>
      </DataPanel>
      {(error || jobQuery.error) && (
        <p role="alert" className="text-destructive">
          {error || jobQuery.error?.message}
        </p>
      )}
      {job && (
        <>
          <DataPanel className="space-y-4 p-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <h2 className="font-semibold">
                {name(job.source)} → {name(job.target)}
              </h2>
              <span role="status" aria-live="polite">
                {stages[job.status] || job.status}
              </span>
            </div>
            <details className="rounded-lg border p-3">
              <summary className="cursor-pointer text-sm font-medium">
                数据范围 · {job.counts.length} 类 · 查看数量明细
              </summary>
              <div className="mt-3 overflow-x-auto">
                <table className="w-full text-left text-sm">
                  <thead>
                    <tr>
                      <th className="py-2">数据</th>
                      <th>来源</th>
                      <th>目标已有</th>
                      <th>待处理</th>
                    </tr>
                  </thead>
                  <tbody>
                    {job.counts.map((row) => (
                      <tr key={row.key} className="border-t">
                        <td className="py-2">{row.label}</td>
                        <td>{row.source}</td>
                        <td>{row.target}</td>
                        <td>{row.pending}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </details>
            <div className="space-y-2 rounded-lg border border-amber-500/40 bg-amber-500/5 p-3">
              <h3 className="font-medium">长期记忆将覆盖目标</h3>
              <p className="text-sm">
                长期记忆：来源 {job.memory.source} 条、图片 {job.memory.source_images ?? 0}{' '}
                条；将覆盖目标 {job.memory.target} 条、图片 {job.memory.target_images ?? 0}{' '}
                条。全局和人物共享记忆保留。
              </p>
              <p className="text-sm">
                事实：来源 {job.memory.source_facts ?? 0} 条，覆盖目标{' '}
                {job.memory.target_facts ?? 0} 条；情节：来源 {job.memory.source_episodes ?? 0}{' '}
                条，覆盖目标 {job.memory.target_episodes ?? 0} 条。
              </p>
            </div>
            <p className="text-muted-foreground text-sm">
              目标的收发身份不变，历史保留原平台及发送者。重复迁移通过来源记录去重；插件私有数据不在此范围内。
            </p>
            {!!job.automatic_config?.length && (
              <details className="rounded-lg border p-3">
                <summary className="cursor-pointer text-sm">
                  自动覆盖默认配置 · {job.automatic_config.length} 项
                </summary>
                <div className="mt-3 space-y-4">
                  {job.automatic_config.map((item) => (
                    <div key={item.id}>
                      <h3 className="mb-2 font-medium">{conflictTitle(item)}</h3>
                      <MigrationValue
                        value={item.source}
                        labels={item.field_labels}
                        chatName={name}
                      />
                    </div>
                  ))}
                </div>
              </details>
            )}
            {job.error && (
              <p role="alert" className="text-destructive whitespace-pre-wrap">
                {job.error}
              </p>
            )}
            {job.status === 'completed' && (
              <p>数据已合并，双方聊天流已解除迁移暂停。来源未删除，可在核对结果后自行处理。</p>
            )}
          </DataPanel>
          {job.status === 'preview' && (
            <DataPanel className="space-y-4 p-4">
              <h2 className="font-semibold">
                逐条处理冲突 · 剩余 {unresolved} / {job.conflicts.length}
              </h2>
              <Input
                aria-label="搜索冲突"
                placeholder="搜索表达、黑话或配置项"
                value={conflictSearch}
                onChange={(e) => {
                  setConflictSearch(e.target.value)
                  setConflictPage(1)
                }}
              />
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div role="group" aria-label="冲突类型" className="flex flex-wrap gap-2">
                  {[
                    ['all', '全部'],
                    ['config', '配置'],
                    ['expressions', '表达'],
                    ['jargons', '黑话'],
                    ['learned_commands', '命令'],
                  ].map(([kind, label]) => (
                    <Button
                      key={kind}
                      size="sm"
                      variant={kindFilter === kind ? 'default' : 'outline'}
                      aria-pressed={kindFilter === kind}
                      onClick={() => {
                        setKindFilter(kind)
                        setConflictPage(1)
                      }}
                    >
                      {label}
                    </Button>
                  ))}
                </div>
                <Label className="flex items-center gap-2">
                  <Checkbox
                    checked={onlyPending}
                    onCheckedChange={(value) => {
                      setOnlyPending(value === true)
                      setConflictPage(1)
                    }}
                  />
                  只看未处理
                </Label>
              </div>
              <p className="text-muted-foreground text-sm">
                已处理 {job.conflicts.length - unresolved} / {job.conflicts.length}{' '}
                项。先对比核心内容，证据和使用记录可按需展开。
              </p>
              {!!job.conflicts.length && !filteredConflicts.length && (
                <p className="rounded-md border p-4 text-sm">
                  当前筛选下没有冲突。可切换类型、清空搜索或关闭“只看未处理”。
                </p>
              )}
              <DataPagination
                total={filteredConflicts.length}
                page={currentPage}
                pageSize={5}
                onPageChange={setConflictPage}
              />
              {job.conflicts.length === 0 && <p className="text-sm">没有冲突，可以确认迁移。</p>}
              {filteredConflicts.slice((currentPage - 1) * 5, currentPage * 5).map((item) => (
                <article key={item.id} className="space-y-3 rounded-lg border p-4">
                  <h3 className="font-medium break-words">{conflictTitle(item)}</h3>
                  <div
                    role="group"
                    aria-label={`处理冲突：${item.label}`}
                    className="flex flex-wrap gap-2"
                  >
                    {item.choices.map((choice) => (
                      <Button
                        key={choice}
                        variant={decisions[item.id] === choice ? 'default' : 'outline'}
                        aria-pressed={decisions[item.id] === choice}
                        disabled={busy}
                        onClick={() =>
                          setDecisions((previous) => ({ ...previous, [item.id]: choice }))
                        }
                      >
                        {choices[choice]}
                      </Button>
                    ))}
                  </div>
                  <p className="text-muted-foreground text-xs" role="status">
                    {decisions[item.id]
                      ? `已选择：${choices[decisions[item.id]]}，可随时修改`
                      : '尚未选择处理方式'}
                  </p>
                  <div className="grid gap-3 md:grid-cols-2">
                    {(['source', 'target'] as const).map((side) => (
                      <div key={side} className="bg-muted/20 min-w-0 rounded-lg border p-3">
                        <h4 className="text-muted-foreground mb-2 text-sm">
                          {side === 'source'
                            ? `来源 · ${name(job.source)}`
                            : `目标 · ${name(job.target)}`}
                        </h4>
                        <MigrationValue
                          value={item[side]}
                          labels={item.field_labels}
                          chatName={name}
                        />
                      </div>
                    ))}
                  </div>
                </article>
              ))}
              <DataPagination
                total={filteredConflicts.length}
                page={currentPage}
                pageSize={5}
                onPageChange={setConflictPage}
              />
              <div className="bg-background sticky bottom-0 z-10 space-y-3 rounded-lg border p-4 shadow-sm">
                <p className="text-sm font-medium">
                  {unresolved ? `还有 ${unresolved} 项冲突需要处理` : '冲突已处理，可以确认迁移'}
                </p>
                <Label className="flex items-start gap-3">
                  <Checkbox
                    checked={confirmed}
                    disabled={busy}
                    onCheckedChange={(value) => setConfirmed(value === true)}
                  />
                  <span>我确认目标聊天流的长期记忆将被全量覆盖，来源数据保留。</span>
                </Label>
                <Button
                  disabled={busy || unresolved > 0 || !confirmed}
                  onClick={() => execute.mutate()}
                >
                  执行迁移
                </Button>
                <p className="text-muted-foreground text-sm">
                  执行前再次核对数据；若出现变化，需要重新预览。可以离开页面，任务会在后台继续。
                </p>
              </div>
            </DataPanel>
          )}
        </>
      )}
      <DataPanel className="space-y-3 p-4">
        <div className="flex items-center justify-between">
          <h2 className="font-semibold">迁移记录</h2>
          <Button variant="ghost" size="sm" onClick={() => void history.refetch()}>
            <RefreshCw className="mr-2 size-4" />
            刷新
          </Button>
        </div>
        {history.isError && <p role="alert">{history.error.message}</p>}
        {history.data?.items
          .slice((currentHistoryPage - 1) * 5, currentHistoryPage * 5)
          .map((item) => (
            <div key={item.id} className="flex flex-wrap items-center gap-3 border-t py-3">
              <Button variant="link" disabled={busy} onClick={() => open(item)}>
                {name(item.source)} → {name(item.target)}
              </Button>
              <span className="text-muted-foreground text-sm">
                {stages[item.status] || item.status} · {new Date(item.created_at).toLocaleString()}
              </span>
              {item.status === 'recovery_required' && (
                <Button
                  variant="outline"
                  disabled={recover.isPending}
                  onClick={() => recover.mutate(item.id)}
                >
                  恢复迁移任务
                </Button>
              )}
            </div>
          ))}
        <DataPagination
          total={history.data?.items.length ?? 0}
          page={currentHistoryPage}
          pageSize={5}
          onPageChange={setHistoryPage}
        />
        {history.data?.items.length === 0 && (
          <p className="text-muted-foreground text-sm">暂无迁移记录。</p>
        )}
      </DataPanel>
    </section>
  )
}
