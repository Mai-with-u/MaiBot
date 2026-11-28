import { useId, type ReactNode } from 'react'

import { Button } from '@/components/ui/button'
import { PillNav } from '@/components/ui/pill-nav'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Separator } from '@/components/ui/separator'
import type { Jargon } from '@/types/jargon'
import type { DetailTab } from './JargonScopeEditor'

function scopeLabel(row: Jargon) {
  return row.scope_name || (row.is_global ? '通用词库' : row.chat_name || '当前聊天')
}

function scopeType(row: Jargon) {
  if (row.scope_kind === 'group') return '共享组'
  if (row.scope_kind === 'baseline') return '旧记录'
  return row.scope_kind === 'global' || row.is_global ? '通用词库' : '聊天'
}

function statusLabel(row: Jargon) {
  return row.is_jargon === null ? '积累中' : row.is_jargon ? '黑话' : '不是黑话'
}

export function JargonMeaningOverview({
  rows, current, onOpen, scoped = true, panel, panelTab = 'meaning', disableSelection = false,
}: {
  rows: Jargon[]
  current: Jargon
  onOpen: (version: Jargon, tab: DetailTab) => void
  scoped?: boolean
  panel?: ReactNode
  panelTab?: DetailTab
  disableSelection?: boolean
}) {
  const detailId = useId()
  const visible = rows.filter((row) => Boolean(row.archived) === Boolean(current.archived))
  const selected = visible.find((row) => row.id === current.id) ?? visible[0]
  if (!selected) return <p className="text-muted-foreground text-sm">暂无释义版本</p>

  const universal = current.archived ? undefined : visible.find(row => row.scope_kind === 'global' || row.is_global)
  const others = visible.filter(row => row.id !== universal?.id)
  const differences = universal && universal.is_jargon && universal.meaning?.trim()
    ? others.filter(row => row.is_jargon && row.meaning?.trim() && row.meaning.trim() !== universal.meaning?.trim()).length
    : 0
  const selectScope = (row: Jargon) => {
    if (disableSelection || selected.id === row.id) return
    onOpen(row, panelTab === 'manage' && row.scope_kind === 'global' ? 'evidence' : panelTab)
  }
  return (
    <section aria-label="各作用域释义" className="flex min-h-0 min-w-0 flex-1 flex-col gap-3 sm:flex-row sm:gap-4">
      {!universal && (
        <>
          <nav aria-label="作用域索引" className="flex min-h-0 shrink-0 flex-col gap-2 sm:w-[26%] sm:max-w-72">
            <h3 className="hidden items-center gap-1.5 text-sm font-semibold sm:flex">
              <span>作用域</span>
              <span className="text-muted-foreground text-xs font-normal tabular-nums">
                ({visible.length})
              </span>
            </h3>
            <div className="custom-scrollbar hidden min-h-0 space-y-1 overflow-y-auto pr-1 sm:block">
              {visible.map(row => (
                <button key={row.id} type="button" disabled={disableSelection && selected.id !== row.id}
                  aria-pressed={selected.id === row.id} aria-controls={detailId} aria-label={scopeLabel(row)}
                  className="block w-full px-3 py-2 text-left hover:bg-muted aria-pressed:bg-accent aria-pressed:text-accent-foreground focus-visible:outline-offset-[-2px]"
                  onClick={() => selectScope(row)}>
                  <span className="block break-words font-medium">{scopeLabel(row)}</span>
                  <span className="text-muted-foreground flex items-center gap-2 text-xs">
                    <span>{scopeType(row)}</span>
                    <span>{statusLabel(row)}</span>
                  </span>
                </button>
              ))}
            </div>
            <div className="sm:hidden">
              <Select value={String(selected.id)} disabled={disableSelection} onValueChange={value => {
                const row = visible.find(item => String(item.id) === value)
                if (row) selectScope(row)
              }}>
                <SelectTrigger aria-label="作用域"><SelectValue /></SelectTrigger>
                <SelectContent>{visible.map(row => <SelectItem key={row.id} value={String(row.id)}>{scopeLabel(row)}</SelectItem>)}</SelectContent>
              </Select>
            </div>
          </nav>
          <Separator variant="groove" orientation="vertical" className="hidden sm:block" />
          <Separator variant="groove" className="sm:hidden" />
        </>
      )}
      <article id={detailId} aria-label={scopeLabel(selected)} className="flex min-h-0 min-w-0 flex-1 flex-col gap-3">
        <header className="flex shrink-0 flex-wrap items-start justify-between gap-2">
          <div className="min-w-0">
            <h3 className="truncate text-base font-semibold" title={scopeLabel(selected)}>{scopeLabel(selected)}</h3>
            <div className="text-muted-foreground mt-0.5 flex flex-wrap items-center gap-x-2.5 gap-y-1 text-xs">
              <span>{statusLabel(selected)}</span>
              <span className="tabular-nums">遇见 {selected.count} 次</span>
              <span className="tabular-nums">
                {selected.last_inference_count ? `上次推断于第 ${selected.last_inference_count} 次` : '尚未推断'}
              </span>
              {!selected.allow_update && <span>已暂停更新</span>}
              {selected.is_complete && <span>推断完成</span>}
              {selected.archived && <span>历史版本</span>}
              {selected.is_legacy_empty_meaning && <span>旧数据</span>}
            </div>
          </div>
          {scoped && panelTab === 'meaning' && (
            <div className="flex shrink-0 flex-wrap gap-1">
              <Button variant="ghost" size="xs" disabled={disableSelection} onClick={() => onOpen(selected, 'evidence')}>线索来源</Button>
              {selected.scope_kind !== 'global' && (
                <Button variant="ghost" size="xs" disabled={disableSelection} onClick={() => onOpen(selected, 'manage')}>管理作用域</Button>
              )}
            </div>
          )}
        </header>
        {panel ?? (
          <p className="custom-scrollbar min-h-0 flex-1 overflow-y-auto text-[0.95rem] leading-[1.85] [overflow-wrap:anywhere] whitespace-pre-wrap">
            {selected.meaning || '暂无释义'}
          </p>
        )}
        {universal && others.length > 0 && (
          <details key={selected.id} open={selected.id !== universal.id} className="shrink-0">
            <summary className="w-fit cursor-pointer text-sm font-medium">
              其他作用域（{others.length}）
              {differences > 0 && <span className="ml-2 text-xs text-muted-foreground">{differences} 个释义不同</span>}
            </summary>
            {(() => {
              const scopeNavRows = selected.id === universal.id ? others : [universal, ...others]
              return (
                <PillNav
                  as="nav"
                  variant="pill"
                  size="xs"
                  scrollable
                  ariaLabel="作用域索引"
                  value={String(selected.id)}
                  onChange={(value) => {
                    const row = scopeNavRows.find((item) => String(item.id) === value)
                    if (row) selectScope(row)
                  }}
                  options={scopeNavRows.map((row) => ({
                    value: String(row.id),
                    ariaLabel: scopeLabel(row),
                    title: scopeLabel(row),
                    disabled: disableSelection,
                    label: (
                      <span className="inline-flex max-w-full items-center gap-1">
                        <span className="truncate">{scopeLabel(row)}</span>
                        <span className="text-muted-foreground">{statusLabel(row)}</span>
                      </span>
                    ),
                  }))}
                  className="mt-1.5"
                  itemClassName="max-w-full"
                />
              )
            })()}
          </details>
        )}
      </article>
    </section>
  )
}
