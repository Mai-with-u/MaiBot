import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useId, useState, type ReactNode } from 'react'

import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Label } from '@/components/ui/label'
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { JargonMeaningOverview } from './JargonMeaningOverview'
import { formatChatDisplayName } from '@/lib/chat-display'
import {
  changeJargonScope,
  getJargonEvidence,
  getJargonVersions,
  type JargonScopeAction,
} from '@/lib/jargon-api'
import type { Jargon, JargonChatInfo, JargonGroupInfo } from '@/types/jargon'

export type DetailTab = 'meaning' | 'evidence' | 'manage'

export function JargonScopeEditor({
  jargon,
  groups,
  chats,
  onChange,
  dirty,
  busy,
  onChangingScope,
  onSelect,
  tab,
  onTabChange,
  children,
}: {
  children: (revisions: Record<number, number>) => ReactNode
  tab: DetailTab
  onTabChange: (tab: DetailTab) => void
  onSelect: (row: Jargon, tab: DetailTab) => void
  onChangingScope: (pending: boolean) => void
  busy: boolean
  dirty: boolean
  jargon: Jargon
  groups: JargonGroupInfo[]
  chats: JargonChatInfo[]
  onChange: (value: Jargon | null) => void
}) {
  const queryClient = useQueryClient()
  const inheritGroupId = useId()
  const [page, setPage] = useState(0)
  const [action, setAction] = useState<JargonScopeAction>(jargon.archived ? 'split' : 'move')
  const [destination, setDestination] = useState('')
  const [extra, setExtra] = useState<number[]>([])
  const [inherit, setInherit] = useState<number>()
  const history = Boolean(jargon.archived)
  const key = ['jargon', 'versions', jargon.content, history]
  const versions = useQuery({ queryKey: key, queryFn: () => getJargonVersions(jargon.content, history) })
  const [editVersions, setEditVersions] = useState<Jargon[]>()
  useEffect(() => {
    if (versions.data && (!dirty || !editVersions)) setEditVersions(versions.data)
  }, [dirty, versions.data, editVersions])
  const revisions = Object.fromEntries((editVersions ?? []).filter(row => !row.archived).map(row => [row.id, row.revision ?? 0]))
  const evidence = useQuery({
    queryKey: ['jargon', 'evidence', jargon.id, page],
    queryFn: () => getJargonEvidence(jargon.id, page * 20),
    enabled: tab === 'evidence',
  })
  const rows = versions.data ?? [jargon]
  const current = rows.find((r) => r.id === jargon.id) ?? jargon
  const active = rows.filter((r) => !r.archived)
  const universal = active.find((r) => r.scope_kind === 'global')
  const universalDestination = JSON.stringify(['global', ''])
  const promoting = destination === universalDestination
  const target = active.find((r) => JSON.stringify([r.scope_kind, r.scope_id]) === destination)
  const merging = !promoting && action !== 'split' && (extra.length > 0 || !!target)
  const sources = rows.filter(
    (r) => r.id === jargon.id || (merging && (r.id === target?.id || extra.includes(r.id)))
  )
  const locked = sources.filter((r) => !r.allow_update)
  const winner =
    sources.find((r) => r.id === inherit) ??
    (locked.length === 1
      ? locked[0]
      : new Set(
            sources.map((r) =>
              JSON.stringify([
                r.meaning,
                r.is_jargon,
                r.is_complete,
                r.count,
                r.last_inference_count,
              ])
            )
          ).size === 1
        ? (locked[0] ?? current)
        : undefined)
  const allShared = groups.some((group) => group.is_global)
  const groupedChats = new Set(groups.flatMap((group) => group.session_ids))
  const destinations = [
    ...groups.map((g) => ({ label: `共享组 · ${g.name}${g.is_global ? '（所有聊天）' : ''}`, value: JSON.stringify(['group', g.id]) })),
    ...chats.filter((c) => !allShared && !groupedChats.has(c.session_id)).map((c) => ({
      label: formatChatDisplayName(c.chat_name, c.account_id),
      value: JSON.stringify(['chat', c.session_id]),
    })),
  ]
  const change = useMutation({
    onMutate: () => onChangingScope(true),
    onSettled: () => onChangingScope(false),
    mutationFn: () => {
      const [kind, id] = JSON.parse(destination) as [string, string]
      return changeJargonScope({
        action: merging ? 'merge' : action,
        kind,
        target: id,
        versions: Object.fromEntries(sources.map((r) => [r.id, r.revision ?? 0])),
        inherit_id: winner?.id ?? current.id,
      })
    },
    onSuccess: async (result) => {
      await queryClient.invalidateQueries({ queryKey: ['jargon'] })
      const fresh = await queryClient.ensureQueryData({
        queryKey: key,
        queryFn: () => getJargonVersions(jargon.content, history),
      })
      onChange(fresh.find((r) => r.id === result.id) ?? null)
    },
  })
  const scopeTools = (
    <Tabs
      className="custom-scrollbar min-h-0 min-w-0 overflow-y-auto"
      value={tab}
      onValueChange={(value) => onTabChange(value as DetailTab)}
    >
      <TabsList aria-label="词条详情" className="max-w-full">
        <TabsTrigger size="sm" value="meaning" disabled={busy}>
          释义
        </TabsTrigger>
        <TabsTrigger size="sm" value="evidence" disabled={busy}>
          线索来源
        </TabsTrigger>
        <TabsTrigger
          size="sm"
          value="manage"
          disabled={busy || dirty || current.scope_kind === 'global'}
          title={dirty ? '请先保存或撤销修改' : undefined}
        >
          管理作用域
        </TabsTrigger>
      </TabsList>
      <TabsContent value="evidence" className="mt-4">
        <div className="space-y-2 text-xs">
          {evidence.isPending && <p role="status">正在加载线索…</p>}
          {evidence.error && <p role="alert">{evidence.error.message}</p>}
          {evidence.data?.length === 0 && <p>{page ? '没有更多学习记录' : '暂无学习记录'}</p>}
          {evidence.data?.map((e) => (
            <details key={e.id} className="rounded border px-3 py-2">
              <summary className="cursor-pointer text-sm">
                {e.source_name} · {e.recorded_at ? new Date(e.recorded_at).toLocaleString() : '历史线索'}
              </summary>
              <div className="mt-2 space-y-3 text-sm">
                {e.excerpt && <p className="whitespace-pre-wrap break-words">{e.excerpt}</p>}
                {e.passages?.map((passage, index) => (
                  <ul key={index} className="space-y-2">
                    {passage.map((message, i) => (
                      <li key={i}>
                        <div className="text-xs text-muted-foreground">
                          {message.sender} · <time dateTime={message.time}>{new Date(message.time).toLocaleString()}</time>
                        </div>
                        <p className="whitespace-pre-wrap break-words">{message.text}</p>
                      </li>
                    ))}
                  </ul>
                ))}
                {!!e.unavailable && <p className="text-muted-foreground">{e.unavailable} 条消息无法还原原文</p>}
                {!e.excerpt && !e.passages?.length && !e.unavailable && (
                  <p className="text-muted-foreground">
                    {e.passages === undefined && e.evidence ? '这条线索只返回了消息引用，尚未取得聊天原文' : '这条线索没有可读的原文'}
                  </p>
                )}
              </div>
            </details>
          ))}
          {(page > 0 || evidence.data?.length === 20) && (
            <div className="flex gap-2">
              <Button
                size="xs"
                variant="outline"
                disabled={!page}
                onClick={() => setPage(page - 1)}
              >
                上一页
              </Button>
              <Button
                size="xs"
                variant="outline"
                disabled={evidence.data?.length !== 20}
                onClick={() => setPage(page + 1)}
              >
                下一页
              </Button>
            </div>
          )}
        </div>
      </TabsContent>
      <TabsContent value="manage" className="mt-4">
        <fieldset disabled={busy} className="min-w-0 space-y-3">
          <Label htmlFor="scope_action">归属操作</Label>
          <Select
            value={action}
            onValueChange={(v) => {
              setAction(v as JargonScopeAction)
              setDestination('')
              setExtra([])
              setInherit(undefined)
            }}
          >
            <SelectTrigger id="scope_action">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {!current.archived && <SelectItem value="move">移动或合并释义</SelectItem>}
              <SelectItem value="split">复制到其他作用域</SelectItem>
            </SelectContent>
          </Select>
          <div className="space-y-1.5">
            <Label htmlFor="scope_destination">目标作用域</Label>
            <Select
              value={destination}
              onValueChange={(v) => {
                setDestination(v)
                setExtra([])
                setInherit(undefined)
              }}
            >
              <SelectTrigger id="scope_destination">
                <SelectValue placeholder="选择目标作用域" />
              </SelectTrigger>
              <SelectContent>
                {action === 'move' && (
                  <SelectItem value={universalDestination} disabled={!!universal}>
                    {universal ? '通用词库已有同名词条' : '转为通用词条'}
                  </SelectItem>
                )}
                {destinations.map((d) => (
                  <SelectItem key={d.value} value={d.value}>
                    {d.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          {promoting && <p className="text-muted-foreground text-xs">转换后无法转回普通词条。各聊天优先使用通用释义，默认允许用新线索更新。</p>}
          {action === 'move' && !promoting &&
            active
              .filter((r) => r.scope_kind !== 'global' && r.id !== current.id && r.id !== target?.id)
              .map((r) => (
                <Label key={r.id} className="flex items-center gap-1 text-xs">
                  <Checkbox
                    checked={extra.includes(r.id)}
                    onCheckedChange={(v) => {
                      setExtra(v === true ? [...extra, r.id] : extra.filter((id) => id !== r.id))
                      setInherit(undefined)
                    }}
                  />
                  同时合入 {r.scope_name}
                </Label>
              ))}
          {merging && (
            <fieldset className="space-y-1.5">
              <legend className="text-xs font-medium">继承释义与推断进度</legend>
              <RadioGroup
                name="inherit-jargon"
                value={winner ? String(winner.id) : ''}
                onValueChange={(value) => setInherit(Number(value))}
                className="gap-2"
              >
                {sources.map((r) => {
                  const optionId = `${inheritGroupId}-${r.id}`
                  return (
                    <div key={r.id} className="flex items-start gap-2">
                      <RadioGroupItem
                        id={optionId}
                        value={String(r.id)}
                        size="sm"
                        className="mt-0.5"
                      />
                      <Label
                        htmlFor={optionId}
                        className="cursor-pointer text-xs leading-relaxed font-normal"
                      >
                        {r.scope_name} · {!r.allow_update ? '已暂停更新 · ' : ''}
                        遇见 {r.count} 次，上次推断 {r.last_inference_count} 次
                        <br />
                        {r.meaning || '暂无释义'}
                      </Label>
                    </div>
                  )
                })}
              </RadioGroup>
            </fieldset>
          )}
          {change.error && <p role="alert">{change.error.message}</p>}
          <Button
            size="sm"
            disabled={
              dirty ||
              change.isPending ||
              !versions.data ||
              sources.length === 0 ||
              !destination ||
              (promoting && !!universal) ||
              (merging && !winner) ||
              (action === 'split' && !!target) ||
              (target?.id === current.id && !extra.length)
            }
            onClick={() => change.mutate()}
          >
            {promoting ? '转为通用词条' : merging ? '确认合并' : '确认变更'}
          </Button>
        </fieldset>
      </TabsContent>
    </Tabs>
  )
  return (
    <>
      <JargonMeaningOverview
        rows={rows}
        current={jargon}
        onOpen={onSelect}
        panel={tab !== 'meaning' ? scopeTools : jargon.archived ? undefined : children(revisions)}
        panelTab={tab}
        disableSelection={dirty || busy || versions.isPending}
      />
      {versions.isPending && <p role="status" className="text-muted-foreground text-xs">正在读取作用域…</p>}
      {versions.error && <p role="alert">{versions.error.message}</p>}
    </>
  )
}
