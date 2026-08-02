import { useEffect, useMemo, useState, type CSSProperties } from 'react'

import { Plus, RefreshCw, Search, Trash2 } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { fieldTitleClassName } from '@/components/dynamic-form/fieldStyle'
import { getChatStreams, type ChatStream } from '@/lib/chat-management-api'
import { resolveLocalizedText } from '@/lib/config-label'
import type { FieldHookComponent } from '@/lib/field-hooks'
import {
  buildAMemorixRetrievalChatTokenOptions,
  resolveAMemorixRetrievalChatsCopy,
} from './AMemorixRetrievalChatsHook.utils'

const normalizeTokenList = (value: unknown): string[] => {
  if (!Array.isArray(value)) {
    return []
  }
  return value.map((item) => String(item ?? '').trim()).filter((item) => item.length > 0)
}

export const AMemorixRetrievalChatsHook: FieldHookComponent = ({
  fieldPath,
  onChange,
  schema,
  value,
}) => {
  const tokens = useMemo(() => normalizeTokenList(value), [value])
  const tokenSet = useMemo(() => new Set(tokens), [tokens])
  const [chats, setChats] = useState<ChatStream[]>([])
  const [loading, setLoading] = useState(false)
  const [errorText, setErrorText] = useState('')
  const [query, setQuery] = useState('')
  const [addDialogOpen, setAddDialogOpen] = useState(false)
  const [pendingTokens, setPendingTokens] = useState<string[]>([])

  const loadChats = async () => {
    try {
      setLoading(true)
      setErrorText('')
      setChats(await getChatStreams())
    } catch (error) {
      setErrorText(error instanceof Error ? error.message : '获取聊天流失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void loadChats()
  }, [])

  const options = useMemo(() => buildAMemorixRetrievalChatTokenOptions(chats), [chats])
  const optionByToken = useMemo(
    () => new Map(options.map((option) => [option.token, option])),
    [options],
  )
  const filteredOptions = useMemo(() => {
    const cleanQuery = query.trim().toLowerCase()
    if (!cleanQuery) {
      return options
    }
    return options.filter((option) =>
      `${option.label} ${option.description} ${option.token}`.toLowerCase().includes(cleanQuery),
    )
  }, [options, query])

  const emitTokens = (nextTokens: string[]) => {
    onChange?.(Array.from(new Set(nextTokens.map((item) => item.trim()).filter(Boolean))))
  }

  const setDialogOpen = (open: boolean) => {
    setAddDialogOpen(open)
    if (!open) {
      setQuery('')
      setPendingTokens([])
    }
  }

  const togglePendingToken = (token: string) => {
    if (tokenSet.has(token)) return
    setPendingTokens((current) =>
      current.includes(token)
        ? current.filter((item) => item !== token)
        : [...current, token],
    )
  }

  const addPendingTokens = () => {
    if (pendingTokens.length === 0) return
    emitTokens([...tokens, ...pendingTokens])
    setDialogOpen(false)
  }

  const fieldLabel =
    schema && 'label' in schema
      ? resolveLocalizedText(schema.label, undefined, '聊天流列表')
      : '聊天流列表'
  const retrievalCopy = resolveAMemorixRetrievalChatsCopy(fieldPath)
  const isEntryFilter = fieldPath === 'a_memorix.filter.chats'
  const defaultDescription = isEntryFilter
    ? '选择要应用到入口聊天过滤的聊天流规则。'
    : '选择要应用到当前跨聊天流检索结果类型的聊天流规则。'
  const rawDescription = schema && 'description' in schema ? schema.description : ''
  const fieldDescription =
    typeof rawDescription === 'string' && rawDescription && rawDescription !== '聊天流列表'
      ? rawDescription
      : defaultDescription

  return (
    <div className="min-w-0 space-y-3">
      <div className="space-y-1">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <Label className={fieldTitleClassName(schema, 'text-[15px] leading-6')}>
            {retrievalCopy.title}
          </Label>
          <Badge variant="outline" className="shrink-0">
            {retrievalCopy.badge}
          </Badge>
        </div>
        <p className="text-xs leading-5 text-muted-foreground">
          {fieldDescription} {retrievalCopy.helperText}
        </p>
        {fieldLabel !== '聊天流列表' && fieldLabel !== retrievalCopy.title && (
          <p className="text-[11px] leading-4 text-muted-foreground/80">
            配置字段：{fieldLabel}
          </p>
        )}
      </div>

      <div className="flex min-w-0 flex-wrap items-center justify-between gap-2">
        <div className="text-sm text-muted-foreground">
          已选择 {tokens.length} 个聊天流规则
        </div>
        <Button
          type="button"
          variant="outline"
          className="shrink-0"
          onClick={() => setDialogOpen(true)}
        >
          <Plus className="mr-2 h-4 w-4" />
          添加聊天流
        </Button>
      </div>

      {errorText && (
        <div className="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">
          {errorText}
        </div>
      )}

      {tokens.length > 0 ? (
        <div className="space-y-2">
          {tokens.map((token) => {
            const option = optionByToken.get(token)
            return (
              <div
                key={token}
                className="flex min-w-0 items-center gap-3 rounded-md border bg-muted/20 px-3 py-2.5"
              >
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm font-medium">{option?.label ?? token}</div>
                  <div className="truncate text-xs text-muted-foreground">
                    {option?.description ?? '历史聊天流 token；可保留或手动移除'}
                  </div>
                </div>
                <Badge variant="outline" className="shrink-0">
                  {option ? '精确聊天流' : '历史 token'}
                </Badge>
                <Button
                  type="button"
                  size="icon"
                  variant="ghost"
                  className="h-8 w-8 shrink-0 text-destructive hover:text-destructive"
                  aria-label={`删除 ${token}`}
                  onClick={() => emitTokens(tokens.filter((item) => item !== token))}
                >
                  <Trash2 className="h-4 w-4" />
                </Button>
              </div>
            )
          })}
        </div>
      ) : (
        <div className="rounded-md border border-dashed px-3 py-3 text-sm text-muted-foreground">
          {retrievalCopy.emptyText} blacklist 模式下表示不屏蔽任何聊天流，whitelist 模式下表示不允许任何聊天流。
        </div>
      )}

      <Dialog open={addDialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent style={{ '--dialog-width': '42rem' } as CSSProperties}>
          <DialogHeader>
            <DialogTitle>添加聊天流</DialogTitle>
            <DialogDescription>
              仅添加已存在的真实聊天流，并保存为精确的 stream ID。
            </DialogDescription>
          </DialogHeader>
          <div className="flex min-w-0 gap-2">
            <div className="relative min-w-0 flex-1">
              <Search className="pointer-events-none absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
              <Input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="搜索群名、私聊、平台或 ID"
                className="pl-8"
              />
            </div>
            <Button type="button" variant="outline" disabled={loading} onClick={() => void loadChats()}>
              <RefreshCw className={loading ? 'mr-2 h-4 w-4 animate-spin' : 'mr-2 h-4 w-4'} />
              刷新
            </Button>
          </div>
          <DialogBody className="max-h-[22rem] rounded-md border">
            <div className="space-y-1 p-2">
              {filteredOptions.length > 0 ? (
                filteredOptions.map((option) => {
                  const alreadySelected = tokenSet.has(option.token)
                  const checked = alreadySelected || pendingTokens.includes(option.token)
                  return (
                    <label
                      key={option.key}
                      className={`flex min-w-0 items-center gap-3 rounded-md px-2 py-2 transition-colors ${
                        alreadySelected ? 'cursor-not-allowed opacity-55' : 'cursor-pointer hover:bg-muted/70'
                      }`}
                    >
                      <Checkbox
                        checked={checked}
                        disabled={alreadySelected}
                        onCheckedChange={() => togglePendingToken(option.token)}
                        aria-label={`选择 ${option.token}`}
                      />
                      <div className="min-w-0 flex-1">
                        <div className="truncate text-sm font-medium">{option.label}</div>
                        <div className="truncate text-xs text-muted-foreground">{option.description}</div>
                      </div>
                      {alreadySelected && <Badge variant="secondary">已添加</Badge>}
                    </label>
                  )
                })
              ) : (
                <div className="px-3 py-8 text-center text-sm text-muted-foreground">
                  {loading ? '正在加载聊天流...' : '没有匹配的真实聊天流。'}
                </div>
              )}
            </div>
          </DialogBody>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setDialogOpen(false)}>
              取消
            </Button>
            <Button type="button" disabled={pendingTokens.length === 0} onClick={addPendingTokens}>
              添加 {pendingTokens.length} 个聊天流
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
