import type { ComponentPropsWithoutRef, ElementRef } from 'react'
import { forwardRef } from 'react'

import { cn } from '@/lib/utils'

import { BackgroundLayer } from '@/components/background-layer'
import { DialogContent } from '@/components/ui/dialog'

import { useBackground } from '@/hooks/use-background'

type DialogContentWithBackgroundProps = ComponentPropsWithoutRef<typeof DialogContent>

export const DialogContentWithBackground = forwardRef<
  ElementRef<typeof DialogContent>,
  DialogContentWithBackgroundProps
>(({ className, children, ...props }, ref) => {
  const bg = useBackground('dialog')

  return (
    <DialogContent ref={ref} className={cn('relative', className)} {...props}>
      <BackgroundLayer config={bg} layerId="dialog" />
      {children}
    </DialogContent>
  )
})

DialogContentWithBackground.displayName = 'DialogContentWithBackground'
