import * as React from 'react'

import { cn } from '@/lib/utils'

/** 列表中的可选行：和操作按钮分开，选中、禁用及焦点样式由主题统一提供。 */
const SelectionListItem = React.forwardRef<
  HTMLButtonElement,
  React.ButtonHTMLAttributes<HTMLButtonElement> & { selected?: boolean; density?: 'default' | 'compact' }
>(
  ({ selected = false, density = 'default', className, ...props }, ref) => (
    <button
      ref={ref}
      type="button"
      data-dashboard-list-item="true"
      data-selected={selected}
      data-density={density}
      aria-pressed={selected}
      className={cn(
        'w-full min-w-0 px-2 py-2 text-left text-sm transition-colors disabled:cursor-not-allowed disabled:opacity-50',
        className
      )}
      {...props}
    />
  )
)
SelectionListItem.displayName = 'SelectionListItem'

export { SelectionListItem }
