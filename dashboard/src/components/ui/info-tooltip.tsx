import type { ReactNode } from 'react'
import { Info } from 'lucide-react'

import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from './tooltip'

export function InfoTooltip({ content, label }: { content: ReactNode; label: string }) {
  return (
    <TooltipProvider delayDuration={200}>
      <Tooltip>
        <TooltipTrigger asChild>
          <button
            type="button"
            aria-label={label}
            data-dashboard-info-trigger="true"
            className="text-muted-foreground hover:text-foreground focus-visible:outline-ring inline-flex size-[18px] shrink-0 cursor-help items-center justify-center rounded-full focus-visible:outline-2 focus-visible:outline-offset-2"
          >
            <Info aria-hidden="true" className="size-4" />
          </button>
        </TooltipTrigger>
        <TooltipContent className="max-w-xs text-sm leading-relaxed shadow-none">{content}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  )
}
