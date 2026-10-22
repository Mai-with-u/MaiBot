import type { ReactNode } from 'react'
import { ChevronDown } from 'lucide-react'
import { cn } from '@/lib/utils'
import { Button } from './button'
import { controlSizeClassNames, type ControlSize } from './control-size'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from './dropdown-menu'

export interface SplitDropdownOption {
  value: string
  label: string
  description?: string
  disabled?: boolean
}

export interface SplitDropdownAction {
  id: string
  label: string
  ariaLabel?: string
  icon?: ReactNode
  disabled?: boolean
  onSelect: () => void
}

interface SplitDropdownSelectProps {
  value: string
  onValueChange: (value: string) => void
  options: SplitDropdownOption[]
  actions: SplitDropdownAction[]
  label: string
  icon?: ReactNode
  placeholder?: string
  disabled?: boolean
  controlSize?: ControlSize
  className?: string
  contentClassName?: string
}

/** 内凹选择框入口；菜单左侧为操作按钮，右侧为可滚动的单选列表。 */
export function SplitDropdownSelect({
  value,
  onValueChange,
  options,
  actions,
  label,
  icon,
  placeholder = '请选择',
  disabled,
  controlSize = 'default',
  className,
  contentClassName,
}: SplitDropdownSelectProps) {
  const selectedOption = options.find((option) => option.value === value)

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        data-dashboard-select-trigger="true"
        className={cn(
          'border-input ring-offset-background focus:ring-ring flex w-full min-w-0 cursor-pointer items-center justify-between rounded-md border bg-transparent px-3 py-2 text-sm whitespace-nowrap shadow-sm focus:ring-1 focus:outline-none disabled:cursor-not-allowed disabled:opacity-50',
          controlSizeClassNames[controlSize],
          className,
        )}
        aria-label={label}
        disabled={disabled}
      >
        {icon && <span className="mr-2 shrink-0 text-muted-foreground [&_svg]:size-4">{icon}</span>}
        <span className="min-w-0 flex-1 truncate">{selectedOption?.label ?? placeholder}</span>
        <ChevronDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className={cn('flex w-[420px] max-w-[calc(100vw-2rem)] p-0', contentClassName)}>
        <DropdownMenuGroup aria-label={`${label}操作`} className="flex shrink-0 flex-col gap-2 border-r p-2">
          {actions.map((action) => (
            <DropdownMenuItem key={action.id} asChild disabled={action.disabled} onSelect={action.onSelect}>
              <Button type="button" variant="outline" size="sm" disabled={action.disabled} aria-label={action.ariaLabel ?? action.label}>
                {action.icon}
                {action.label}
              </Button>
            </DropdownMenuItem>
          ))}
        </DropdownMenuGroup>
        <DropdownMenuRadioGroup
          value={value}
          onValueChange={onValueChange}
          aria-label={`${label}列表`}
          className="max-h-[min(320px,var(--radix-dropdown-menu-content-available-height))] min-w-0 flex-1 overflow-y-auto p-1"
        >
          {options.map((option) => (
            <DropdownMenuRadioItem key={option.value} value={option.value} disabled={option.disabled}>
              <span className="min-w-0">
                <span className="block break-words">{option.label}</span>
                {option.description && <span className="block text-xs text-muted-foreground">{option.description}</span>}
              </span>
            </DropdownMenuRadioItem>
          ))}
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
