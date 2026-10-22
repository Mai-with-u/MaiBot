import * as React from 'react'

import { cn } from '@/lib/utils'
import { controlSizeClassNames, type ControlSize } from './control-size'

const Input = React.forwardRef<HTMLInputElement, React.ComponentProps<'input'> & { controlSize?: ControlSize }>(
  ({ className, type, controlSize = 'default', ...props }, ref) => {
    return (
      <input
        type={type}
        data-dashboard-input="true"
        className={cn(
          'border-input file:text-foreground placeholder:text-muted-foreground focus-visible:ring-ring flex w-full rounded-md border bg-transparent px-3 py-1 text-base shadow-sm transition-colors file:border-0 file:bg-transparent file:text-sm file:font-medium focus-visible:ring-1 focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-50 md:text-sm',
          controlSizeClassNames[controlSize],
          className
        )}
        ref={ref}
        {...props}
      />
    )
  }
)
Input.displayName = 'Input'

export { Input }
