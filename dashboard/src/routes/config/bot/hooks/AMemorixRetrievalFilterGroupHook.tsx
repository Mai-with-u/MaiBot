import { Badge } from '@/components/ui/badge'
import type { FieldHookComponent } from '@/lib/field-hooks'

import type { RetrievalFilterKind } from './AMemorixRetrievalChatsHook.utils'

type RetrievalFilterMode = 'blacklist' | 'whitelist'

interface RetrievalSubtypeFilterValue {
  chats: string[]
  enabled: boolean
  mode: RetrievalFilterMode
}

const RETRIEVAL_FILTER_KINDS: Array<{ kind: RetrievalFilterKind, label: string }> = [
  { kind: 'chat_stream', label: '普通聊天流' },
  { kind: 'chat_summary', label: '聊天总结' },
  { kind: 'episode', label: 'Episode' },
]

const getObjectValue = (value: unknown): Record<string, unknown> => {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {}
}

const normalizeEnabledValue = (value: unknown): boolean => {
  if (typeof value === 'boolean') {
    return value
  }
  return String(value ?? '').trim().toLowerCase() === 'true'
}

const normalizeSubtypeFilterValue = (
  retrievalValue: Record<string, unknown>,
  kind: RetrievalFilterKind,
): RetrievalSubtypeFilterValue => {
  const subtypeConfig = getObjectValue(retrievalValue[kind])
  const rawMode = String(subtypeConfig.mode ?? '').trim()
  return {
    chats: Array.isArray(subtypeConfig.chats)
      ? subtypeConfig.chats.map((item) => String(item ?? '').trim()).filter(Boolean)
      : [],
    enabled: normalizeEnabledValue(subtypeConfig.enabled),
    mode: rawMode === 'whitelist' ? 'whitelist' : 'blacklist',
  }
}

export const AMemorixRetrievalFilterGroupHook: FieldHookComponent = ({
  children,
  value,
}) => {
  const retrievalValue = getObjectValue(value)
  const summaries = RETRIEVAL_FILTER_KINDS.map(({ kind, label }) => ({
    kind,
    label,
    value: normalizeSubtypeFilterValue(retrievalValue, kind),
  }))

  return (
    <div className="min-w-0 space-y-4">
      <section className="min-w-0 rounded-md border bg-muted/10 px-3 py-3">
        <div className="flex min-w-0 flex-col gap-3 md:flex-row md:items-start md:justify-between">
          <div className="min-w-0 space-y-1">
            <div className="flex min-w-0 flex-wrap items-center gap-2">
              <div className="text-sm font-medium">跨聊天流检索结果过滤</div>
              <Badge variant="outline" className="shrink-0">高级</Badge>
            </div>
            <p className="text-xs leading-5 text-muted-foreground">
              这是一组全局高级规则，只处理其他聊天流来源的已召回结果。本聊天流读取自身记忆会直接放行。
            </p>
          </div>
        </div>

        <div className="mt-3 grid min-w-0 gap-2 md:grid-cols-3">
          {summaries.map((item) => (
            <div key={item.kind} className="min-w-0 rounded-md border bg-background/60 px-3 py-2">
              <div className="text-sm font-medium">{item.label}</div>
              <div className="mt-1 text-xs leading-5 text-muted-foreground">
                {item.value.enabled ? '已启用' : '未启用'}，
                {item.value.mode === 'whitelist' ? '白名单' : '黑名单'}，
                {item.value.chats.length} 个来源 token
              </div>
            </div>
          ))}
        </div>
      </section>

      {children}
    </div>
  )
}
