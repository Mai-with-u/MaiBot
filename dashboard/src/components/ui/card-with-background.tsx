import type { ComponentPropsWithoutRef, ElementRef } from 'react'
import { forwardRef } from 'react'

import { cn } from '@/lib/utils'

import { BackgroundLayer } from '@/components/background-layer'
import { Card } from '@/components/ui/card'

import { useBackground } from '@/hooks/use-background'

type CardWithBackgroundProps = ComponentPropsWithoutRef<typeof Card>

export const CardWithBackground = forwardRef<
  ElementRef<typeof Card>,
  CardWithBackgroundProps
>(({ className, children, ...props }, ref) => {
  const bg = useBackground('card')

  return (
    <Card ref={ref} className={cn('relative', className)} {...props}>
      <BackgroundLayer config={bg} layerId="card" />
      {children}
    </Card>
  )
})

CardWithBackground.displayName = 'CardWithBackground'
