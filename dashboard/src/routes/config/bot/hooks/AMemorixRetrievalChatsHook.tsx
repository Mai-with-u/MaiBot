import { useEffect, useMemo, useState } from 'react'

import { Check, Plus, RefreshCw, Search, Trash2 } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { fieldTitleClassName } from '@/components/dynamic-form/fieldStyle'
import { resolveLocalizedText } from '@/lib/config-label'
import { getMemoryImportChatTargets, type MemoryImportChatTargetPayload } from '@/lib/memory-api'
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
  const [targets, setTargets] = useState<MemoryImportChatTargetPayload[]>([])
  const [loading, setLoading] = useState(false)
  const [errorText, setErrorText] = useState('')
  const [query, setQuery] = useState('')
  const [manualToken, setManualToken] = useState('')

  const loadTargets = async () => {
    try {
      setLoading(true)
      setErrorText('')
      const payload = await getMemoryImportChatTargets()
      setTargets(payload.data ?? [])
    } catch (error) {
      setErrorText(error instanceof Error ? error.message : '获取聊天流失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void loadTargets()
  }, [])

  const options = useMemo(() => buildAMemorixRetrievalChatTokenOptions(targets), [targets])
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

  const toggleToken = (token: string, checked: boolean) => {
    emitTokens(checked ? [...tokens, token] : tokens.filter((item) => item !== token))
  }

  const addManualToken = () => {
    const cleanToken = manualToken.trim()
    if (!cleanToken) {
      return
    }
    emitTokens([...tokens, cleanToken])
    setManualToken('')
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

      <div className="flex min-w-0 flex-col gap-2 md:flex-row">
        <div className="relative min-w-0 flex-1">
          <Search className="pointer-events-none absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
          <Input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜索群名、私聊、ID 或 token"
            className="pl-8"
          />
        </div>
        <Button
          type="button"
          variant="outline"
          className="shrink-0"
          disabled={loading}
          onClick={() => void loadTargets()}
        >
          <RefreshCw className={loading ? 'mr-2 h-4 w-4 animate-spin' : 'mr-2 h-4 w-4'} />
          刷新
        </Button>
      </div>

      <div className="rounded-md border bg-muted/10">
        <ScrollArea className="h-64" scrollbars="vertical">
          <div className="space-y-1 p-2">
            {filteredOptions.length > 0 ? (
              filteredOptions.map((option) => {
                const checked = tokenSet.has(option.token)
                return (
                  <label
                    key={option.key}
                    className="flex min-w-0 cursor-pointer items-center gap-3 rounded-md px-2 py-2 transition-colors hover:bg-muted/70"
                  >
                    <Checkbox
                      checked={checked}
                      onCheckedChange={(nextChecked) => toggleToken(option.token, Boolean(nextChecked))}
                      aria-label={`选择 ${option.token}`}
                    />
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-sm font-medium">{option.label}</div>
                      <div className="truncate text-xs text-muted-foreground">{option.description}</div>
                    </div>
                    <Badge variant="outline" className="shrink-0 font-mono">
                      {option.token}
                    </Badge>
                  </label>
                )
              })
            ) : (
              <div className="px-3 py-8 text-center text-sm text-muted-foreground">
                {loading ? '正在加载聊天流...' : '没有匹配的聊天流，可在下方手动添加 token。'}
              </div>
            )}
          </div>
        </ScrollArea>
      </div>

      {errorText && (
        <div className="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">
          {errorText}
        </div>
      )}

      <div className="flex min-w-0 flex-col gap-2 md:flex-row">
        <Select
          value=""
          onValueChange={(token) => {
            if (token) {
              emitTokens([...tokens, token])
            }
          }}
        >
          <SelectTrigger className="min-w-0 md:w-72">
            <SelectValue placeholder="从已知聊天流快速添加" />
          </SelectTrigger>
          <SelectContent>
            {options.map((option) => (
              <SelectItem key={option.key} value={option.token}>
                {option.token}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Input
          value={manualToken}
          onChange={(event) => setManualToken(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') {
              event.preventDefault()
              addManualToken()
            }
          }}
          placeholder="手动添加，如 group:123 或 stream:session_id"
          className="min-w-0 flex-1"
        />
        <Button type="button" variant="outline" className="shrink-0" onClick={addManualToken}>
          <Plus className="mr-2 h-4 w-4" />
          添加
        </Button>
      </div>

      {tokens.length > 0 ? (
        <div className="flex min-w-0 flex-wrap gap-2">
          {tokens.map((token) => (
            <Badge key={token} variant="secondary" className="max-w-full gap-1 font-mono">
              <Check className="h-3 w-3 shrink-0" />
              <span className="min-w-0 truncate">{token}</span>
              <button
                type="button"
                className="ml-1 inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-sm text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                aria-label={`删除 ${token}`}
                onClick={() => emitTokens(tokens.filter((item) => item !== token))}
              >
                <Trash2 className="h-3 w-3" />
              </button>
            </Badge>
          ))}
        </div>
      ) : (
        <div className="rounded-md border border-dashed px-3 py-3 text-sm text-muted-foreground">
          {retrievalCopy.emptyText} blacklist 模式下表示不屏蔽任何聊天流，whitelist 模式下表示不允许任何聊天流。
        </div>
      )}
    </div>
  )
}
