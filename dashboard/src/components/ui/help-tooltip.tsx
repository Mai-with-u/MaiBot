import * as React from "react"
import { HelpCircle } from "lucide-react"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"

interface HelpTooltipProps {
  content: React.ReactNode
  className?: string
  iconClassName?: string
  side?: "top" | "right" | "bottom" | "left"
  align?: "start" | "center" | "end"
  maxWidth?: string
}

export function HelpTooltip({
  content,
  className,
  iconClassName,
  side = "top",
  align = "center",
  maxWidth = "300px",
}: HelpTooltipProps) {
  return (
    <TooltipProvider delayDuration={200}>
      <Tooltip>
        <TooltipTrigger asChild>
          <button
            type="button"
            className={cn(
              "inline-flex items-center justify-center rounded-full",
              "text-muted-foreground hover:text-foreground",
              "transition-colors cursor-help",
              "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
              className
            )}
            onClick={(e) => e.preventDefault()}
          >
            <HelpCircle className={cn("h-4 w-4", iconClassName)} />
            <span className="sr-only">帮助信息</span>
          </button>
        </TooltipTrigger>
        <TooltipContent
          side={side}
          align={align}
          className={cn(
            "max-w-[var(--max-width)] text-sm leading-relaxed",
            "bg-background text-foreground",
            "border-2 border-primary shadow-lg",
            "p-4"
          )}
          style={{ "--max-width": maxWidth } as React.CSSProperties}
        >
          {content}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  )
}
