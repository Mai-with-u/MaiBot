import * as React from 'react'
import * as SwitchPrimitives from '@radix-ui/react-switch'

import { cn } from '@/lib/utils'

const Switch = React.forwardRef<
  React.ElementRef<typeof SwitchPrimitives.Root>,
  React.ComponentPropsWithoutRef<typeof SwitchPrimitives.Root> & { size?: 'default' | 'lg' }
>(({ className, size = 'default', ...props }, ref) => (
  <SwitchPrimitives.Root
    data-dashboard-switch="true"
    data-control-size={size}
    className={cn(
      'peer focus-visible:ring-ring focus-visible:ring-offset-background data-[state=checked]:bg-primary data-[state=unchecked]:bg-input inline-flex h-[var(--switch-height)] w-[var(--switch-width)] shrink-0 cursor-pointer items-center rounded-full border-2 border-transparent shadow-sm transition-colors focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-50',
      className
    )}
    {...props}
    ref={ref}
  >
    <SwitchPrimitives.Thumb
      data-dashboard-switch-thumb="true"
      className={cn(
        'bg-background pointer-events-none block h-[var(--switch-thumb)] w-[var(--switch-thumb)] rounded-full shadow-lg ring-0 transition-transform data-[state=checked]:translate-x-[var(--switch-travel)] data-[state=unchecked]:translate-x-0'
      )}
    />
  </SwitchPrimitives.Root>
))
Switch.displayName = SwitchPrimitives.Root.displayName

export { Switch }
