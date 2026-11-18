import * as React from 'react'
import * as SwitchPrimitives from '@radix-ui/react-switch'

import { cn } from '@/lib/utils'
import { controlSizeClassNames, type ControlSize } from './control-size'

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

/** 工具栏里与按钮、下拉框等高的开关：文字和开关装进同一个控件框，整框都能点。 */
const SwitchField = React.forwardRef<
  React.ElementRef<typeof SwitchPrimitives.Root>,
  Omit<React.ComponentPropsWithoutRef<typeof SwitchPrimitives.Root>, 'children'> & {
    label: React.ReactNode
    controlSize?: ControlSize
  }
>(({ className, label, controlSize = 'default', disabled, ...props }, ref) => {
  // 高档位换大号开关；右侧内距与上下空隙接近，开关像嵌在框的右端。
  const large = controlSize === 'default' || controlSize === 'lg'
  return (
    <label
      data-dashboard-switch-field="true"
      data-control-size={controlSize}
      className={cn(
        'border-input inline-flex shrink-0 cursor-pointer items-center gap-2.5 rounded-md border pl-3 text-sm whitespace-nowrap shadow-sm select-none',
        large ? 'pr-2' : 'pr-1.5',
        controlSizeClassNames[controlSize],
        disabled && 'cursor-not-allowed opacity-50',
        className
      )}
    >
      <span data-dashboard-switch-field-label="true">{label}</span>
      <Switch ref={ref} size={large ? 'lg' : 'default'} disabled={disabled} {...props} />
    </label>
  )
})
SwitchField.displayName = 'SwitchField'

export { Switch, SwitchField }
