import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Check, ChevronsUpDown, X } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Command, CommandInput, CommandItem, CommandList } from '@/components/ui/command'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { backendApi, requireSuccess } from '@/lib/http'

export interface ConfigTarget {
  platform: string
  type: 'group' | 'user'
  id: string
  name?: string
}

const targetKey = (target: ConfigTarget) => JSON.stringify([target.platform, target.type, target.id])

/** 插件和宿主共用的结构化目标选择器；名称仅作展示，身份由三元组确定。 */
export function TargetSelect({ value, onChange, type, platform, disabled, label }: {
  value: ConfigTarget[]
  onChange: (targets: ConfigTarget[]) => void
  type: 'group' | 'user'
  platform?: string
  disabled?: boolean
  label: string
}) {
  const [open, setOpen] = useState(false)
  const [input, setInput] = useState('')
  const [search, setSearch] = useState('')
  useEffect(() => {
    const timer = window.setTimeout(() => setSearch(input.trim()), 250)
    return () => window.clearTimeout(timer)
  }, [input])
  const query = useQuery({
    queryKey: ['config-targets', type, platform, search],
    queryFn: async ({ signal }) => requireSuccess(await backendApi.get<{ success: boolean; targets: ConfigTarget[] }>(
      '/api/chat/targets', { query: { target_type: type, platform, search, limit: 50 }, signal }
    ), '加载目标失败').targets,
    enabled: open && !disabled,
    staleTime: 30_000,
  })
  return <div className="min-w-0 space-y-2">
    <Popover open={open && !disabled} onOpenChange={setOpen}>
      <PopoverTrigger asChild><Button type="button" variant="outline" size="sm" disabled={disabled} role="combobox" aria-expanded={open} aria-label={label} className="group w-full justify-between gap-2 font-normal">
        <span className="truncate">搜索并选择{type === 'group' ? '群' : '用户'}</span>
        <ChevronsUpDown strokeWidth={1.75} className="size-3.5 shrink-0 text-muted-foreground/70 transition-colors group-hover:text-foreground/80" />
      </Button></PopoverTrigger>
      <PopoverContent align="start" className="w-[var(--radix-popover-trigger-width)] min-w-64 p-0">
        <Command shouldFilter={false}>
          <CommandInput placeholder="搜索名称或 ID" value={input} onValueChange={setInput} />
          <CommandList>
            {query.isFetching ? <p role="status" className="p-3 text-xs text-muted-foreground">正在搜索…</p>
              : query.isError ? <div role="alert" className="p-3 text-xs text-destructive">{query.error.message}<Button size="xs" variant="ghost" onClick={() => query.refetch()}>重试</Button></div>
              : query.data?.length === 0 ? <p className="p-3 text-xs text-muted-foreground">没有匹配的目标</p>
              : query.data?.map(target => {
                const selected = value.some(item => targetKey(item) === targetKey(target))
                return <CommandItem key={targetKey(target)} value={targetKey(target)} onSelect={() => onChange(selected
                  ? value.filter(item => targetKey(item) !== targetKey(target)) : [...value, target])}>
                  <Check className={`mr-2 h-4 w-4 shrink-0 ${selected ? '' : 'opacity-0'}`} />
                  <div className="min-w-0"><div className="truncate">{target.name || target.id}</div>
                    <div className="truncate text-xs text-muted-foreground">{target.platform} · {target.id}</div></div>
                </CommandItem>
              })}
          </CommandList>
          {query.data?.length === 50 && <p className="border-t px-3 py-2 text-xs text-muted-foreground">显示前 50 项，输入名称可继续查找</p>}
        </Command>
      </PopoverContent>
    </Popover>
    {value.length > 0 && <div className="flex flex-wrap gap-1.5">
      {value.map(target => <Badge key={targetKey(target)} variant="secondary" className="max-w-full gap-1 font-normal">
        <span className="truncate" title={`${target.platform} · ${target.id}`}>{target.name || target.id} · {target.platform}</span>
        <button type="button" className="shrink-0" disabled={disabled} aria-label={`移除 ${target.name || target.id}`} onClick={() => onChange(value.filter(item => targetKey(item) !== targetKey(target)))}>
          <X className="h-3 w-3 shrink-0" />
        </button>
      </Badge>)}
    </div>}
  </div>
}
