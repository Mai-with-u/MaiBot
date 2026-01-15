import * as React from "react"
import { cva, type VariantProps } from "class-variance-authority"

import { cn } from "@/lib/utils"

const kbdVariants = cva(
  "pointer-events-none inline-flex select-none items-center gap-1 rounded border bg-muted px-1.5 font-mono font-medium opacity-100",
  {
    variants: {
      size: {
        sm: "h-5 text-[10px]",
        default: "h-6 text-xs",
        lg: "h-7 text-sm",
      },
    },
    defaultVariants: {
      size: "default",
    },
  }
)

export interface KbdProps
  extends React.HTMLAttributes<HTMLElement>,
    VariantProps<typeof kbdVariants> {
  abbrTitle?: string
}

const Kbd = React.forwardRef<HTMLElement, KbdProps>(
  ({ className, size, abbrTitle, children, ...props }, ref) => {
    return (
      <kbd
        className={cn(kbdVariants({ size, className }))}
        ref={ref}
        {...props}
      >
        {abbrTitle ? <abbr title={abbrTitle}>{children}</abbr> : children}
      </kbd>
    )
  }
)
Kbd.displayName = "Kbd"

export { Kbd }
