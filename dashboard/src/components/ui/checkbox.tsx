import { Check, Minus } from 'lucide-react'
import * as CheckboxPrimitive from '@radix-ui/react-checkbox'
import * as React from 'react'

import { cn } from '@/lib/utils'

const Checkbox = React.forwardRef<
  React.ElementRef<typeof CheckboxPrimitive.Root>,
  React.ComponentPropsWithoutRef<typeof CheckboxPrimitive.Root> & { size?: 'default' | 'sm' }
>(({ className, size = 'default', ...props }, ref) => (
  <CheckboxPrimitive.Root
    ref={ref}
    data-dashboard-checkbox="true"
    data-control-size={size}
    className={cn(
      'grid place-content-center peer h-[var(--choice-size)] w-[var(--choice-size)] shrink-0 cursor-pointer rounded-sm border border-primary shadow focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50 data-[state=checked]:bg-primary data-[state=checked]:text-primary-foreground data-[state=indeterminate]:bg-primary data-[state=indeterminate]:text-primary-foreground',
      className
    )}
    {...props}
  >
    <CheckboxPrimitive.Indicator
      data-dashboard-checkbox-indicator="true"
      className={cn('grid place-content-center text-current')}
    >
      <Check data-checkbox-mark="checked" aria-hidden="true" />
      <Minus data-checkbox-mark="indeterminate" aria-hidden="true" />
    </CheckboxPrimitive.Indicator>
  </CheckboxPrimitive.Root>
))
Checkbox.displayName = CheckboxPrimitive.Root.displayName

export { Checkbox }
