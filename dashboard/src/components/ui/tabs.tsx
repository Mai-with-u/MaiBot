'use client'

import * as React from 'react'
import * as TabsPrimitive from '@radix-ui/react-tabs'

import { ModernTabIndicator, MotionTabContent } from '@/components/motion'
import { cn } from '@/lib/utils'

const Tabs = TabsPrimitive.Root

function assignRef<T>(ref: React.ForwardedRef<T>, value: T) {
  if (typeof ref === 'function') {
    ref(value)
  } else if (ref) {
    ref.current = value
  }
}

const TabsList = React.forwardRef<
  React.ElementRef<typeof TabsPrimitive.List>,
  React.ComponentPropsWithoutRef<typeof TabsPrimitive.List>
>(({ className, children, ...props }, ref) => {
  const listRef = React.useRef<HTMLDivElement | null>(null)
  const setRefs = React.useCallback(
    (node: HTMLDivElement | null) => {
      listRef.current = node
      assignRef(ref, node)
    },
    [ref]
  )

  return (
    <TabsPrimitive.List
      ref={setRefs}
      data-dashboard-tabs-list="true"
      className={cn(
        'bg-muted text-muted-foreground relative inline-flex h-9 items-center justify-center rounded-lg p-1',
        className
      )}
      {...props}
    >
      <ModernTabIndicator listRef={listRef} />
      {children}
    </TabsPrimitive.List>
  )
})
TabsList.displayName = TabsPrimitive.List.displayName

const TabsTrigger = React.forwardRef<
  React.ElementRef<typeof TabsPrimitive.Trigger>,
  React.ComponentPropsWithoutRef<typeof TabsPrimitive.Trigger> & { size?: 'default' | 'sm' }
>(({ className, size = 'default', ...props }, ref) => (
  <TabsPrimitive.Trigger
    ref={ref}
    data-dashboard-tabs-trigger="true"
    data-control-size={size}
    className={cn(
      'ring-offset-background focus-visible:ring-ring data-[state=active]:bg-background data-[state=active]:text-foreground inline-flex cursor-pointer items-center justify-center rounded-md px-3 py-1 text-sm font-medium whitespace-nowrap focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:outline-none disabled:pointer-events-none disabled:opacity-50 data-[state=active]:shadow',
      size === 'sm' ? 'min-h-[var(--tab-height-sm)]' : 'min-h-[var(--tab-height)]',
      className
    )}
    {...props}
  />
))
TabsTrigger.displayName = TabsPrimitive.Trigger.displayName

const TabsContent = React.forwardRef<
  React.ElementRef<typeof TabsPrimitive.Content>,
  React.ComponentPropsWithoutRef<typeof TabsPrimitive.Content>
>(({ className, children, asChild = false, ...props }, ref) => {
  const mergedClassName = cn(
    'ring-offset-background focus-visible:ring-ring mt-2 focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:outline-none',
    className
  )

  if (asChild) {
    return (
      <TabsPrimitive.Content ref={ref} asChild className={mergedClassName} {...props}>
        {children}
      </TabsPrimitive.Content>
    )
  }

  return (
    <TabsPrimitive.Content ref={ref} asChild {...props}>
      <MotionTabContent className={mergedClassName}>{children}</MotionTabContent>
    </TabsPrimitive.Content>
  )
})
TabsContent.displayName = TabsPrimitive.Content.displayName

export { Tabs, TabsList, TabsTrigger, TabsContent }
