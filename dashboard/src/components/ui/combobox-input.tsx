import { type KeyboardEvent, type ReactNode, useId, useMemo, useState } from 'react'
import { Check, ChevronDown, Loader2, RefreshCw } from 'lucide-react'

import { Input } from '@/components/ui/input'
import { Popover, PopoverAnchor, PopoverContent } from '@/components/ui/popover'
import { cn } from '@/lib/utils'

export interface ComboboxOption {
  value: string
  /** 第二行的补充说明，与 value 相同时不显示。 */
  description?: string
}

interface ComboboxInputProps {
  id?: string
  value: string
  onValueChange: (value: string) => void
  options?: ComboboxOption[]
  placeholder?: string
  invalid?: boolean
  loading?: boolean
  /** 列表获取失败时的说明，显示在列表里，输入框仍可手写。 */
  error?: ReactNode
  emptyText?: string
  /** 提供时在列表底部显示「刷新列表」。 */
  onRefresh?: () => void
  className?: string
}

/** 可输入的下拉框：直接输入任意值，或从按输入内容筛选的列表中选择。 */
export function ComboboxInput({
  id,
  value,
  onValueChange,
  options,
  placeholder,
  invalid,
  loading = false,
  error,
  emptyText = '没有匹配的选项，可直接使用输入的内容',
  onRefresh,
  className,
}: ComboboxInputProps) {
  const listId = useId()
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState<string | null>(null)
  const [activeIndex, setActiveIndex] = useState(-1)
  const hasList = options !== undefined || loading || Boolean(error)

  // 打开列表时显示全部；在框里打字后才按输入筛选。
  const filtered = useMemo(() => {
    const keyword = (query ?? '').trim().toLowerCase()
    if (!options || !keyword) return options ?? []
    return options.filter((option) =>
      option.value.toLowerCase().includes(keyword) || option.description?.toLowerCase().includes(keyword)
    )
  }, [options, query])

  const openList = (fromTyping: boolean) => {
    if (!hasList) return
    if (!fromTyping) setQuery(null)
    setActiveIndex(-1)
    setOpen(true)
  }
  const select = (option: ComboboxOption) => {
    onValueChange(option.value)
    setOpen(false)
    setQuery(null)
  }
  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (!hasList) return
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault()
      if (!open) {
        openList(false)
        return
      }
      const step = event.key === 'ArrowDown' ? 1 : -1
      setActiveIndex((index) => filtered.length === 0 ? -1 : (index + step + filtered.length) % filtered.length)
    } else if (event.key === 'Enter' && open && activeIndex >= 0 && filtered[activeIndex]) {
      event.preventDefault()
      select(filtered[activeIndex])
    } else if (event.key === 'Escape' && open) {
      event.preventDefault()
      event.stopPropagation()
      setOpen(false)
    }
  }
  const activeId = activeIndex >= 0 ? `${listId}-${activeIndex}` : undefined

  return (
    <Popover open={open && hasList} onOpenChange={setOpen}>
      <PopoverAnchor asChild>
        <div data-combobox-input="true" className={cn('relative min-w-0', className)}>
          <Input
            id={id}
            value={value}
            role={hasList ? 'combobox' : undefined}
            aria-expanded={hasList ? open : undefined}
            aria-controls={hasList ? listId : undefined}
            aria-autocomplete={hasList ? 'list' : undefined}
            aria-activedescendant={open ? activeId : undefined}
            aria-invalid={invalid || undefined}
            autoComplete="off"
            placeholder={placeholder}
            className={cn(hasList && 'pr-10', invalid && 'border-destructive focus-visible:ring-destructive')}
            onChange={(event) => {
              onValueChange(event.target.value)
              setQuery(event.target.value)
              if (!open) openList(true)
              else setActiveIndex(-1)
            }}
            onKeyDown={onKeyDown}
          />
          {hasList && (
            <button
              type="button"
              tabIndex={-1}
              aria-label={open ? '收起列表' : '展开列表'}
              data-combobox-toggle="true"
              className="absolute inset-y-0 right-0 flex w-9 items-center justify-center text-muted-foreground hover:text-foreground"
              onClick={() => (open ? setOpen(false) : openList(false))}
            >
              {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <ChevronDown className="h-4 w-4" />}
            </button>
          )}
        </div>
      </PopoverAnchor>
      <PopoverContent
        align="start"
        className="z-[60] p-1"
        style={{ width: 'var(--radix-popper-anchor-width)' }}
        // 焦点留在输入框里，边看列表边打字。
        onOpenAutoFocus={(event) => event.preventDefault()}
        onInteractOutside={(event) => {
          if ((event.target as HTMLElement | null)?.closest?.('[data-combobox-input]')) event.preventDefault()
        }}
      >
        <div id={listId} role="listbox" className="max-h-72 overflow-y-auto">
          {loading && (
            <p className="flex items-center gap-2 px-2 py-3 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              正在获取列表…
            </p>
          )}
          {!loading && error && <p className="px-2 py-3 text-xs text-destructive">{error}</p>}
          {!loading && !error && filtered.length === 0 && (
            <p className="px-2 py-3 text-xs text-muted-foreground">{emptyText}</p>
          )}
          {!loading && filtered.map((option, index) => (
            <div
              key={option.value}
              id={`${listId}-${index}`}
              role="option"
              aria-selected={option.value === value}
              data-highlighted={index === activeIndex ? '' : undefined}
              // 键盘焦点始终留在输入框，选项只靠 aria-activedescendant 指向，因此不进 Tab 顺序。
              tabIndex={-1}
              onKeyDown={(event) => {
                if (event.key === 'Enter') select(option)
              }}
              className="relative flex cursor-pointer select-none flex-col rounded-sm px-2 py-1.5 pr-8 text-sm data-[highlighted]:bg-accent data-[highlighted]:text-accent-foreground hover:bg-accent hover:text-accent-foreground"
              onMouseEnter={() => setActiveIndex(index)}
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => select(option)}
            >
              <span className="truncate" title={option.value}>{option.value}</span>
              {option.description && option.description !== option.value && (
                <span className="truncate text-xs text-muted-foreground">{option.description}</span>
              )}
              {option.value === value && <Check className="absolute top-2 right-2 h-4 w-4" />}
            </div>
          ))}
        </div>
        {onRefresh && (
          <button
            type="button"
            className="mt-1 flex w-full items-center gap-2 border-t px-2 pt-2 pb-1 text-xs text-muted-foreground hover:text-foreground disabled:opacity-50"
            disabled={loading}
            onMouseDown={(event) => event.preventDefault()}
            onClick={onRefresh}
          >
            <RefreshCw className="h-3.5 w-3.5" />
            {error ? '重试' : '刷新列表'}
          </button>
        )}
      </PopoverContent>
    </Popover>
  )
}
