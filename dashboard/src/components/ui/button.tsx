import * as React from 'react'
import { Slot } from '@radix-ui/react-slot'
import { cva, type VariantProps } from 'class-variance-authority'

import { cn } from '@/lib/utils'

const buttonVariants = cva(
  'dashboard-button inline-flex cursor-pointer items-center justify-center gap-2 whitespace-nowrap rounded-md text-sm font-medium aria-pressed:bg-secondary aria-pressed:text-secondary-foreground transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 [&_svg]:pointer-events-none [&_svg]:size-4 [&_svg]:shrink-0',
  {
    variants: {
      variant: {
        default: 'dashboard-button--default bg-primary text-primary-foreground shadow hover:bg-primary/90',
        destructive: 'dashboard-button--destructive bg-destructive text-destructive-foreground shadow-sm hover:bg-destructive/90',
        outline:
          'dashboard-button--outline border border-input bg-background shadow-sm hover:bg-accent hover:text-accent-foreground',
        secondary: 'dashboard-button--secondary bg-secondary text-secondary-foreground shadow-sm hover:bg-secondary/80',
        ghost: 'dashboard-button--ghost hover:bg-accent hover:text-accent-foreground',
        link: 'dashboard-button--link text-primary underline-offset-4 hover:underline',
      },
      size: {
        default: 'h-[var(--control-height)] px-4 py-2',
        sm: 'h-[var(--control-height-sm)] rounded-md px-3 text-xs',
        xs: 'h-[var(--control-height-xs)] rounded-md px-2 text-xs',
        lg: 'h-[var(--control-height-lg)] rounded-md px-8',
        icon: 'h-[var(--control-height)] w-[var(--control-height)] p-0',
        'icon-sm': 'h-[var(--control-height-sm)] w-[var(--control-height-sm)] p-0',
        'icon-xs': 'h-[var(--control-height-xs)] w-[var(--control-height-xs)] p-0',
      },
    },
    defaultVariants: {
      variant: 'default',
      size: 'default',
    },
  }
)

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>, VariantProps<typeof buttonVariants> {
  asChild?: boolean
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : 'button'
    return (
      <Comp
        className={cn(buttonVariants({ variant, size, className }))}
        data-dashboard-button="true"
        ref={ref}
        {...props}
      />
    )
  }
)
Button.displayName = 'Button'

// eslint-disable-next-line react-refresh/only-export-components
export { Button, buttonVariants }
