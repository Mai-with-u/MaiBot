import * as React from 'react'
import * as ProgressPrimitive from '@radix-ui/react-progress'

import { cn } from '@/lib/utils'

const Progress = React.forwardRef<
  React.ElementRef<typeof ProgressPrimitive.Root>,
  React.ComponentPropsWithoutRef<typeof ProgressPrimitive.Root>
>(({ className, value, max = 100, ...props }, ref) => {
  const limit = Number.isFinite(max) && max > 0 ? max : 100
  const progress = typeof value === 'number' && Number.isFinite(value) ? Math.min(limit, Math.max(0, value)) : null
  return (
    <ProgressPrimitive.Root
      ref={ref}
      data-dashboard-progress="true"
      value={progress}
      max={limit}
      className={cn('bg-primary/20 relative h-2 w-full overflow-hidden rounded-full', className)}
      {...props}
    >
      <ProgressPrimitive.Indicator
        data-dashboard-progress-indicator="true"
        className="bg-primary h-full w-full flex-1 transition-all"
        style={progress === null ? undefined : { transform: `translateX(-${100 - progress / limit * 100}%)` }}
      />
    </ProgressPrimitive.Root>
  )
})
Progress.displayName = ProgressPrimitive.Root.displayName

export { Progress }
