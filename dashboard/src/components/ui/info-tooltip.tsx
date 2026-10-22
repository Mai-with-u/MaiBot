import type { ReactNode } from 'react'

import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from './tooltip'

export function InfoTooltip({ content, label }: { content: ReactNode; label: string }) {
  return (
    <TooltipProvider delayDuration={200}>
      <Tooltip>
        <TooltipTrigger asChild>
          <span
            tabIndex={0}
            aria-label={label}
            className="border-foreground/70 bg-background text-foreground focus-visible:ring-ring inline-flex size-[18px] shrink-0 cursor-help items-center justify-center rounded-none border-2 focus-visible:outline-none focus-visible:ring-2"
            style={{
              backgroundColor: 'color-mix(in srgb, var(--color-background) 92%, white)',
              boxShadow: 'inset 1px 1px 0 rgb(255 255 255 / 70%), inset -1px -1px 0 rgb(0 0 0 / 18%), 1px 1px 0 rgb(0 0 0 / 25%)',
            }}
          >
            <svg aria-hidden="true" viewBox="0 0 12 12" className="size-2.5" shapeRendering="crispEdges">
              <path fill="currentColor" d="M5 1h2v2H5zM3 4h4v5h2v2H3V9h2V6H3z" />
            </svg>
          </span>
        </TooltipTrigger>
        <TooltipContent className="max-w-xs border-2 text-sm leading-relaxed">{content}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  )
}
