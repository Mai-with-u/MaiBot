import { Plus } from 'lucide-react'

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { cn } from '@/lib/utils'
import { MOBILE_GLASS_CLASS } from './mobile-glass'

export interface MobileAddMenuItem {
  label: string
  onSelect: () => void
}

interface MobileAddMenuProps {
  label: string
  items: MobileAddMenuItem[]
}

/** 页头右上角的「+」玻璃按钮，点开是一组添加操作 */
export function MobileAddMenu({ label, items }: MobileAddMenuProps) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          aria-label={label}
          data-mobile-header-button="true"
          className={cn(
            'text-foreground flex h-11 w-11 items-center justify-center rounded-full data-[state=open]:rotate-45 transition-transform',
            MOBILE_GLASS_CLASS
          )}
        >
          <Plus className="h-[22px] w-[22px]" strokeWidth={1.7} aria-hidden="true" />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="end"
        sideOffset={8}
        data-mobile-add-menu="true"
        className={cn('w-60 rounded-3xl border-0 p-1.5', MOBILE_GLASS_CLASS)}
      >
        {items.map((item) => (
          <DropdownMenuItem
            key={item.label}
            onSelect={item.onSelect}
            className="h-12 rounded-[18px] px-4 text-[17px]"
          >
            {item.label}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
