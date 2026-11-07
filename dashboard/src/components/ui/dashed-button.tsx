import { type ComponentPropsWithoutRef, forwardRef } from 'react'

import { cn } from '@/lib/utils'
import '@/styles/dashed-button.css'

export const DashedButton = forwardRef<HTMLButtonElement, ComponentPropsWithoutRef<'button'>>(
  function DashedButton({ className, type = 'button', ...props }, ref) {
    return (
      <button
        {...props}
        ref={ref}
        type={type}
        data-dashed-button="true"
        className={cn('inline-flex h-9 items-center justify-center gap-1.5', className)}
      />
    )
  }
)
